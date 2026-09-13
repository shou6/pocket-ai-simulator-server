"""ジョブを取り出して実行するワーカー。

    uv run python -m pocket_api.jobs.worker

API とは別のプロセスで動かす（数分かかる処理を API のリクエストの中で回さない）。
"""

from __future__ import annotations

import argparse
import logging
import time
import traceback
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session

from pocket_api.jobs import queue
from pocket_api.jobs.runners import Runner, default_runners
from pocket_api.jobs.speed import measure_speed, record_speed
from pocket_api.optimize.progress import WorkStatus

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]


def run_once(
    open_session: SessionFactory,
    runners: Mapping[str, Runner],
    *,
    games_per_second: float | None = None,
) -> bool:
    """待ちのジョブを 1 件実行する。実行したら `True`、無ければ `False`。

    取り出し・進捗・完了はそれぞれ別のトランザクションで書く。
    進捗をすぐにコミットしないと、API から見えない。
    """
    with open_session() as session:
        job = queue.claim_next(session, games_per_second=games_per_second)
        if job is None:
            return False
        job_id, kind, params = job.id, job.kind, dict(job.params)
    logger.info("ジョブ %s（%s）を始めます", job_id, kind)

    def progress(stage: str, detail: str, work: WorkStatus | None = None) -> None:
        with open_session() as session:
            queue.report_progress(session, job_id, stage, detail, work)

    runner = runners.get(kind)
    try:
        if runner is None:
            raise ValueError(f"知らないジョブの種類です: {kind}")
        result: dict[str, Any] = runner(params, progress)
    except Exception as error:
        logger.exception("ジョブ %s が失敗しました", job_id)
        with open_session() as session:
            queue.fail(session, job_id, f"{error}\n\n{traceback.format_exc(limit=5)}")
        return True
    with open_session() as session:
        queue.complete(session, job_id, result)
    logger.info("ジョブ %s が終わりました", job_id)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll", type=float, default=2.0, help="待ちが無いときの確認間隔（秒）")
    parser.add_argument("--once", action="store_true", help="1 件だけ実行して終わる")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from pocket_api.db.session import session_scope

    with session_scope() as session:
        stale = queue.requeue_stale(session)
    if stale:
        logger.info("実行中のまま残っていた %d 件を待ちに戻しました", stale)
    # このマシンの速さを測って残す。所要時間の目安をこの速さに合わせる（`jobs.estimates`）
    games_per_second = measure_speed()
    with session_scope() as session:
        record_speed(session, games_per_second)
    logger.info("対戦の速さ：毎秒 %.1f 試合", games_per_second)
    runners = default_runners(session_scope)
    while True:
        worked = run_once(session_scope, runners, games_per_second=games_per_second)
        if args.once:
            return 0
        if not worked:
            time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
