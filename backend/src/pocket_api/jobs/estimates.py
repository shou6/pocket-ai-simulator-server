"""ジョブの所要時間の目安。これまでに終わったジョブの記録から出す。

定数の目安は、実行する環境の負荷やデッキによって 2〜3 倍外れる（2026-09-13 に「速い」の目安 3 分に
対して 10 分 31 秒かかった）。同じ種類・同じ試合数で終わったジョブがあれば、その所要時間の
中央値を目安にする。記録が無いうちは `runners.FALLBACK_ESTIMATE_SECONDS` を使う。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from pocket_api.db.models import OptimizationJob
from pocket_api.jobs import queue
from pocket_api.jobs.runners import FALLBACK_ESTIMATE_SECONDS, IMPROVE_METHOD

RECENT_JOBS = 10
"""中央値を取る記録の数。新しいものから。"""

GAME_OPTIONS = (60, 200, 800)


@dataclass(frozen=True)
class Estimate:
    seconds: int
    samples: int
    """目安の元にした記録の数。0 なら初期値。"""


def estimate(session: Session, kind: str, games: int) -> Estimate:
    """その種類・試合数のジョブが終わるまでの目安。"""
    rows = session.execute(
        select(OptimizationJob.started_at, OptimizationJob.finished_at)
        .where(
            OptimizationJob.kind == kind,
            OptimizationJob.status == queue.DONE,
            OptimizationJob.params["games"].as_integer() == games,
            OptimizationJob.started_at.is_not(None),
            OptimizationJob.finished_at.is_not(None),
            # 改善はやり方を変えたので、今のやり方の記録だけを使う
            OptimizationJob.params["method"].as_string() == IMPROVE_METHOD
            if kind == "improve"
            else true(),
        )
        .order_by(OptimizationJob.finished_at.desc())
        .limit(RECENT_JOBS)
    ).all()
    durations = [
        (finished - started).total_seconds()
        for started, finished in rows
        if started is not None and finished is not None
    ]
    if not durations:
        return Estimate(seconds=FALLBACK_ESTIMATE_SECONDS.get(kind, {}).get(games, 0), samples=0)
    return Estimate(seconds=round(statistics.median(durations)), samples=len(durations))


def all_estimates(session: Session) -> dict[str, dict[int, Estimate]]:
    """画面の試合数の選択肢ぶん、まとめて出す。"""
    return {
        kind: {games: estimate(session, kind, games) for games in GAME_OPTIONS}
        for kind in FALLBACK_ESTIMATE_SECONDS
    }
