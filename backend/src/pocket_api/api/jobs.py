"""数分かかる処理のジョブ API（`docs/ui-design.md` 3 節）。

    POST /jobs/improve      1 枚入れ替えの改善提案を登録
    POST /jobs/optimize     デッキ提案を登録
    GET  /jobs/{id}         状態と進捗
    GET  /jobs/{id}/result  結果

登録は即時に返し、実行はワーカー（`pocket_api.jobs.worker`）がする。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pocket_api.api.decks import DeckProblem, GameOption, check_deck
from pocket_api.api.deps import get_session
from pocket_api.api.meta import latest_meta
from pocket_api.cards.catalog import card_view
from pocket_api.db.models import OptimizationJob
from pocket_api.jobs import queue
from pocket_api.jobs.estimates import all_estimates, estimate
from pocket_api.jobs.runners import IMPROVE_METHOD, STRATEGY, canonical_params

router = APIRouter(prefix="/jobs", tags=["jobs"])

MAX_AXIS_CARDS = 5
"""軸にできるカードの種類の上限。多すぎると種のデッキが組めない。"""


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    stage: str | None
    detail: str | None
    params: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    estimate_seconds: int | None
    """所要時間の目安。同じ種類・試合数で終わったジョブの中央値。進捗の割合の代わりに出す。"""

    estimate_samples: int = 0
    """目安の元にした記録の数。0 なら初期値（まだ実行した記録が無い）。"""

    reused: bool = False
    """同じ条件のジョブを使い回したか。完了済みなら「前回の結果」として見せる。"""

    error: str | None = None

    @classmethod
    def of(cls, job: OptimizationJob, session: Session, *, reused: bool = False) -> JobOut:
        games = int(job.params.get("games", 0))
        guess = estimate(session, job.kind, games)
        return cls(
            id=job.id,
            kind=job.kind,
            status=job.status,
            stage=job.stage,
            detail=job.detail,
            params=job.params,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            estimate_seconds=guess.seconds or None,
            estimate_samples=guess.samples,
            reused=reused,
            error=job.error.splitlines()[0] if job.error else None,
        )


class ImproveIn(BaseModel):
    decklist: str
    games: GameOption = 200
    excluded: list[str] = Field(default_factory=list)
    """「持っていない」と言われたカード。入れる候補にしない。"""

    force: bool = False
    """前回の結果があってもやり直す。"""


def _register(session: Session, kind: str, params: dict[str, Any], *, force: bool) -> JobOut:
    job, reused = queue.enqueue(
        session, kind, canonical_params(kind, params), strategy=STRATEGY, force=force
    )
    session.commit()
    session.refresh(job)
    return JobOut.of(job, session, reused=reused)


@router.post("/improve", response_model=JobOut, responses={422: {"model": DeckProblem}})
def register_improve(body: ImproveIn, session: Annotated[Session, Depends(get_session)]) -> JobOut:
    """デッキの改善を登録する。上がらなくなるまで 1 枚ずつ入れ替える。"""
    validation = check_deck(body.decklist)
    if not validation.ok:
        raise HTTPException(
            status_code=422,
            detail=DeckProblem(
                message="このデッキは評価できません", validation=validation
            ).model_dump(),
        )
    snapshot, _ = latest_meta(session)
    params = {
        "decklist": body.decklist,
        "games": body.games,
        "excluded": body.excluded,
        "snapshot_id": str(snapshot.id),
        # 1 枚入れ替えだけだった頃の結果を使い回さない
        "method": IMPROVE_METHOD,
    }
    return _register(session, "improve", params, force=body.force)


class AxisCardIn(BaseModel):
    id: str
    count: int = Field(default=2, ge=1, le=2)


class OptimizeIn(BaseModel):
    games: GameOption = 200
    target: str | None = None
    """勝ちたい相手のアーキタイプ（slug）。無ければ環境全体（F-05）。"""

    axis: list[AxisCardIn] = Field(default_factory=list, max_length=MAX_AXIS_CARDS)
    """軸にするカード（F-09）。"""

    excluded: list[str] = Field(default_factory=list)
    force: bool = False


@router.post("/optimize", response_model=JobOut)
def register_optimize(
    body: OptimizeIn, session: Annotated[Session, Depends(get_session)]
) -> JobOut:
    """デッキ提案を登録する。約 8 分。"""
    snapshot, decks = latest_meta(session)
    target_name: str | None = None
    if body.target is not None:
        matched = next((d for d in decks if d.slug == body.target), None)
        if matched is None:
            raise HTTPException(
                status_code=422, detail=f"相手のデッキが見つかりません: {body.target}"
            )
        target_name = matched.name
    required: dict[str, int] = {}
    for card in body.axis:
        view = card_view(card.id)
        if view is None or not view.implemented:
            raise HTTPException(status_code=422, detail=f"軸にできないカードです: {card.id}")
        required[card.id] = max(required.get(card.id, 0), card.count)
    params = {
        "games": body.games,
        "target": target_name,
        "required": required,
        "excluded": body.excluded,
        "snapshot_id": str(snapshot.id),
    }
    return _register(session, "optimize", params, force=body.force)


class EstimateOut(BaseModel):
    seconds: int
    samples: int


class EstimatesOut(BaseModel):
    """ジョブの種類 → 試合数 → 所要時間の目安。画面の試合数の選択肢に添える。"""

    improve: dict[int, EstimateOut]
    optimize: dict[int, EstimateOut]


@router.get("/estimates", response_model=EstimatesOut)
def get_estimates(session: Annotated[Session, Depends(get_session)]) -> EstimatesOut:
    """所要時間の目安。これまでに終わったジョブの記録から出す。"""
    found = all_estimates(session)
    return EstimatesOut(
        improve={
            g: EstimateOut(seconds=e.seconds, samples=e.samples)
            for g, e in found["improve"].items()
        },
        optimize={
            g: EstimateOut(seconds=e.seconds, samples=e.samples)
            for g, e in found["optimize"].items()
        },
    )


def _job(session: Session, job_id: uuid.UUID) -> OptimizationJob:
    job = session.get(OptimizationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return job


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]) -> JobOut:
    """状態と進捗。ブラウザは 3 秒ごとに呼ぶ。"""
    return JobOut.of(_job(session, job_id), session)


class JobResultOut(BaseModel):
    job: JobOut
    result: dict[str, Any]


@router.get("/{job_id}/result", response_model=JobResultOut)
def get_result(
    job_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]
) -> JobResultOut:
    """結果。終わっていなければ 409。"""
    job = _job(session, job_id)
    if job.status != queue.DONE or job.result is None:
        raise HTTPException(status_code=409, detail=f"まだ結果がありません（{job.status}）")
    return JobResultOut(job=JobOut.of(job, session), result=job.result)
