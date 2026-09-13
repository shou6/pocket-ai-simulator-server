"""所要時間の目安。マシンの速さ（`jobs.speed`）と、これまでに終わったジョブの記録から出す。

定数の目安は、実行する環境の負荷やマシンによって数倍外れる（2026-09-13 に「速い」の目安 3 分に
対して 10 分 31 秒、2026-09-14 には CPU 0.5 の本番で「ふつう」が目安の数倍）。そこで

1. 同じ種類・同じ試合数で終わったジョブがあれば、その所要時間を**実行したワーカーの速さで
   仕事の量に直し**、いまのワーカーの速さで割った値の中央値を目安にする
2. 記録が無ければ、手元で測った定数（`runners.FALLBACK_ESTIMATE_SECONDS`）を、それを測ったときの
   速さ（`REFERENCE_SPEED`）といまのワーカーの速さの比で換算する
3. ワーカーがまだ速さを測っていなければ、定数のまま
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from pocket_api.db.models import OptimizationJob
from pocket_api.jobs import queue
from pocket_api.jobs.runners import FALLBACK_ESTIMATE_SECONDS, IMPROVE_METHOD
from pocket_api.jobs.speed import latest_speed

RECENT_JOBS = 10
"""中央値を取る記録の数。新しいものから。"""

GAME_OPTIONS = (60, 200, 800)

REFERENCE_SPEED = 105.0
"""`FALLBACK_ESTIMATE_SECONDS` を測ったときの速さ（`jobs.speed` の物差しで 1 秒あたりの試合数）。

2026-09-13 の手元（i7-8700T、12 スレッド、メモリ確保はシステムの malloc）で、環境のデッキを混ぜた
探索は毎秒 95 試合だった。同じ日の条件で `measure_speed` の対戦は混ぜた場合の 1.1 倍速いので 105。
"""

DIAGNOSE_SPEED_RATIO = 0.9
"""診断（自分のデッキ × 環境の 10 デッキ）の速さ ÷ `measure_speed` の速さ。
2026-09-14 の手元で 169 ÷ 187。"""

Basis = Literal["history", "speed", "default"]


@dataclass(frozen=True)
class Estimate:
    seconds: int
    samples: int
    """目安の元にした記録の数。0 なら記録が無い。"""

    basis: Basis = "default"
    """`history`（記録）/ `speed`（ワーカーの速さで換算した定数）/ `default`（定数のまま）。"""


def estimate(session: Session, kind: str, games: int, *, opponents: int = 10) -> Estimate:
    """その種類・試合数の処理が終わるまでの目安。`kind` は `improve` / `optimize` / `diagnose`。"""
    speed = latest_speed(session)
    if kind == "diagnose":
        per_second = (speed or REFERENCE_SPEED) * DIAGNOSE_SPEED_RATIO
        seconds = max(1, round(opponents * games / per_second))
        return Estimate(seconds=seconds, samples=0, basis="speed" if speed else "default")

    rows = session.execute(
        select(
            OptimizationJob.started_at,
            OptimizationJob.finished_at,
            OptimizationJob.games_per_second,
        )
        .where(
            OptimizationJob.kind == kind,
            OptimizationJob.status == queue.DONE,
            OptimizationJob.params["games"].as_integer() == games,
            OptimizationJob.started_at.is_not(None),
            OptimizationJob.finished_at.is_not(None),
            # 速さを測ってからは、速さの記録があるジョブだけを換算して使う
            OptimizationJob.games_per_second.is_not(None) if speed else true(),
            # 改善はやり方を変えたので、今のやり方の記録だけを使う
            OptimizationJob.params["method"].as_string() == IMPROVE_METHOD
            if kind == "improve"
            else true(),
        )
        .order_by(OptimizationJob.finished_at.desc())
        .limit(RECENT_JOBS)
    ).all()
    durations = [
        (finished - started).total_seconds() * (job_speed / speed if speed and job_speed else 1.0)
        for started, finished, job_speed in rows
        if started is not None and finished is not None
    ]
    if durations:
        return Estimate(
            seconds=round(statistics.median(durations)), samples=len(durations), basis="history"
        )
    fallback = FALLBACK_ESTIMATE_SECONDS.get(kind, {}).get(games, 0)
    if speed:
        return Estimate(seconds=round(fallback * REFERENCE_SPEED / speed), samples=0, basis="speed")
    return Estimate(seconds=fallback, samples=0)


def all_estimates(session: Session, *, opponents: int = 10) -> dict[str, dict[int, Estimate]]:
    """画面の試合数の選択肢ぶん、まとめて出す。"""
    kinds = [*FALLBACK_ESTIMATE_SECONDS, "diagnose"]
    return {
        kind: {games: estimate(session, kind, games, opponents=opponents) for games in GAME_OPTIONS}
        for kind in kinds
    }


def remaining_seconds(job: OptimizationJob, now: datetime) -> int | None:
    """実行中のジョブの残り時間。ワーカーが最後に書いた見込みから、その後の経過を引く。

    仕事の量は上限の見込みなので、探索が早く止まれば実際はもっと短い。
    """
    if job.status != queue.RUNNING or job.remaining_seconds is None:
        return None
    since = (now - job.updated_at).total_seconds() if job.updated_at else 0.0
    return max(0, round(job.remaining_seconds - since))
