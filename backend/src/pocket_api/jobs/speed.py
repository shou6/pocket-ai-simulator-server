"""ワーカーが動くマシンの対戦の速さを測る。

所要時間の目安は、手元（12 スレッド）で測った値のままだと、CPU の少ない本番で数倍外れる
（2026-09-14、Render の CPU 0.5 で「ふつう」の改善が目安の数倍かかった）。
ワーカーが起動時に速さを測って DB に残し、目安をその速さに換算する（`jobs.estimates`）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pocket_engine_py as engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from pocket_api.db.models import WorkerSpeed
from pocket_api.ingest.snapshot import latest_snapshot_path

REPO_ROOT = Path(__file__).resolve().parents[4]
META_DIR = REPO_ROOT / "data" / "meta"

STRATEGY = "l"
ROUND_GAMES = 20
"""1 回に回す試合数。先攻・後攻が半分ずつになるよう偶数にする。"""


def _benchmark_decks(snapshot: Path) -> tuple[str, str]:
    """環境の上位 2 デッキ。探索で実際に回す対戦に近い重さにする。"""
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    lists = [a["decklists"][0] for a in data["archetypes"] if a["decklists"]]
    if len(lists) < 2:
        raise ValueError(f"速さを測るデッキが足りません: {snapshot}")
    return lists[0], lists[1]


def measure_speed(*, min_seconds: float = 3.0, snapshot: Path | None = None) -> float:
    """1 秒あたりの試合数。`min_seconds` 以上回して平均する。"""
    deck_a, deck_b = _benchmark_decks(snapshot or latest_snapshot_path(META_DIR))
    games = 0
    started = time.perf_counter()
    seed = 1
    while True:
        engine.evaluate_matchup(deck_a, deck_b, STRATEGY, STRATEGY, ROUND_GAMES, seed)
        games += ROUND_GAMES
        seed += 1
        elapsed = time.perf_counter() - started
        if elapsed >= min_seconds:
            return games / elapsed


def usable_cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # pragma: no cover - Linux 以外
        return os.cpu_count() or 1


def record_speed(session: Session, games_per_second: float) -> WorkerSpeed:
    row = WorkerSpeed(games_per_second=games_per_second, threads=usable_cpus())
    session.add(row)
    session.flush()
    return row


def latest_speed(session: Session) -> float | None:
    """いちばん新しく測った速さ。まだ測っていなければ `None`。"""
    return session.scalar(
        select(WorkerSpeed.games_per_second).order_by(WorkerSpeed.created_at.desc()).limit(1)
    )
