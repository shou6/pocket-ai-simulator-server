"""PostgreSQL のテーブルを使うジョブキュー（要件 7 章）。

Redis は使わない。ワーカーは `SELECT ... FOR UPDATE SKIP LOCKED` で 1 件ずつ取るので、
何台並べても同じジョブを二重に取らない。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pocket_engine_py as engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from pocket_api.db.models import OptimizationJob
from pocket_api.optimize.progress import WorkStatus

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

STAGES = ("prepare", "evaluate", "finalize")
"""段階は 3 つだけ（`docs/ui-design.md` 3 節）。割合（%）は出さない。"""


def params_key(kind: str, params: dict[str, Any], *, strategy: str) -> str:
    """同じ結果になる条件を 1 つの鍵にする。エンジン版と方策を含める。"""
    payload = json.dumps(
        {
            "kind": kind,
            "params": params,
            "strategy": strategy,
            "engine": engine.deckgym_revision()[:8],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enqueue(
    session: Session,
    kind: str,
    params: dict[str, Any],
    *,
    strategy: str,
    force: bool = False,
) -> tuple[OptimizationJob, bool]:
    """ジョブを登録する。（ジョブ, 前のジョブを使い回したか）を返す。

    同じ条件のジョブが待ち・実行中・完了のどれかであれば、新しく作らずにそれを返す。
    `force` のときは完了済みを使い回さない（「やり直す」）。待ち・実行中は常に使い回す。
    """
    key = params_key(kind, params, strategy=strategy)
    reusable = [QUEUED, RUNNING] if force else [QUEUED, RUNNING, DONE]
    existing = session.scalar(
        select(OptimizationJob)
        .where(OptimizationJob.params_key == key, OptimizationJob.status.in_(reusable))
        .order_by(OptimizationJob.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing, True
    job = OptimizationJob(
        kind=kind,
        params=params,
        params_key=key,
        engine_revision=engine.deckgym_revision()[:8],
        strategy=strategy,
        status=QUEUED,
    )
    session.add(job)
    session.flush()
    return job, False


def claim_next(
    session: Session, *, games_per_second: float | None = None
) -> OptimizationJob | None:
    """待ちのジョブを古い順に 1 件取り、実行中にする。無ければ `None`。

    `games_per_second` はこのワーカーの速さ。所要時間の記録を換算するためにジョブへ残す。
    呼び出し側はすぐにコミットして、ロックを手放すこと。
    """
    job = session.scalar(
        select(OptimizationJob)
        .where(OptimizationJob.status == QUEUED)
        .order_by(OptimizationJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = RUNNING
    job.stage = STAGES[0]
    job.detail = None
    job.work_done = None
    job.work_total = None
    job.remaining_seconds = None
    job.games_per_second = games_per_second
    job.started_at = datetime.now(UTC)
    session.flush()
    return job


def report_progress(
    session: Session,
    job_id: uuid.UUID,
    stage: str,
    detail: str,
    work: WorkStatus | None = None,
) -> None:
    """いまの段階と、済んだ仕事の量・残り時間を書き込む。

    `work` が無ければ前の値を残す。
    """
    if stage not in STAGES:
        raise ValueError(f"知らない段階です: {stage}")
    job = session.get(OptimizationJob, job_id)
    if job is None:
        return
    job.stage = stage
    job.detail = detail[:255]
    if work is not None:
        job.work_done, job.work_total = work.done, work.total
        job.remaining_seconds = work.remaining_seconds
    session.flush()


def complete(session: Session, job_id: uuid.UUID, result: dict[str, Any]) -> None:
    job = session.get(OptimizationJob, job_id)
    if job is None:
        return
    job.status = DONE
    job.result = result
    job.detail = None
    job.finished_at = datetime.now(UTC)
    session.flush()


def fail(session: Session, job_id: uuid.UUID, error: str) -> None:
    job = session.get(OptimizationJob, job_id)
    if job is None:
        return
    job.status = FAILED
    job.error = error
    job.finished_at = datetime.now(UTC)
    session.flush()


def requeue_stale(session: Session) -> int:
    """ワーカーが落ちて実行中のまま残ったジョブを待ちに戻す。起動時に呼ぶ。

    ワーカーが 1 台の前提。複数台にするなら、ハートビートを持たせてから使うこと。
    """
    stale = session.scalars(
        select(OptimizationJob).where(OptimizationJob.status == RUNNING).with_for_update()
    ).all()
    for job in stale:
        job.status = QUEUED
        job.stage = None
        job.detail = None
        job.started_at = None
    session.flush()
    return len(stale)
