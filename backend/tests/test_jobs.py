"""ジョブ（キュー・ワーカー・API）のテスト（docs/ui-design.md 3 節）。"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pocket_api.db.models import OptimizationJob
from pocket_api.ingest.store import import_snapshot
from pocket_api.jobs import queue, runners
from pocket_api.jobs.worker import run_once
from pocket_api.optimize.evaluate import Evaluation
from pocket_api.optimize.progress import WorkMeter, WorkStatus
from pocket_api.optimize.propose import ProposalReport
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.refine import RefineReport, Swap

SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")
SAMPLE = Path("/workspace/data/decks/mydeck_sample003.txt").read_text(encoding="utf-8")


def _opener(session: Session) -> Any:
    @contextmanager
    def open_session() -> Iterator[Session]:
        yield session
        session.flush()

    return open_session


# --- キュー ---------------------------------------------------------------


def test_same_conditions_reuse_the_job_unless_forced(db_session: Session) -> None:
    params = {"decklist": SAMPLE, "games": 200}
    first, reused = queue.enqueue(db_session, "improve", params, strategy="l")
    assert not reused
    again, reused = queue.enqueue(db_session, "improve", params, strategy="l")
    assert reused
    assert again.id == first.id

    queue.complete(db_session, first.id, {"ok": True})
    done, reused = queue.enqueue(db_session, "improve", params, strategy="l")
    assert reused
    assert done.id == first.id, "済んだ結果を返す"
    fresh, reused = queue.enqueue(db_session, "improve", params, strategy="l", force=True)
    assert not reused
    assert fresh.id != first.id, "やり直すときは新しく作る"


def test_different_conditions_make_different_jobs(db_session: Session) -> None:
    a, _ = queue.enqueue(db_session, "improve", {"games": 60}, strategy="l")
    b, _ = queue.enqueue(db_session, "improve", {"games": 200}, strategy="l")
    assert a.id != b.id


def test_claims_the_oldest_queued_job_once(db_session: Session) -> None:
    job, _ = queue.enqueue(db_session, "improve", {"games": 60}, strategy="l")
    claimed = queue.claim_next(db_session)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == queue.RUNNING
    assert claimed.stage == "prepare"
    assert queue.claim_next(db_session) is None


def test_rejects_unknown_stages(db_session: Session) -> None:
    job, _ = queue.enqueue(db_session, "improve", {"games": 60}, strategy="l")
    with pytest.raises(ValueError, match="段階"):
        queue.report_progress(db_session, job.id, "50%", "")


def test_requeues_jobs_left_running(db_session: Session) -> None:
    job, _ = queue.enqueue(db_session, "improve", {"games": 60}, strategy="l")
    queue.claim_next(db_session)
    assert queue.requeue_stale(db_session) == 1
    db_session.refresh(job)
    assert job.status == queue.QUEUED


# --- ワーカー -------------------------------------------------------------


def test_worker_runs_a_job_and_records_progress_and_result(db_session: Session) -> None:
    job, _ = queue.enqueue(db_session, "fake", {"games": 60}, strategy="l")
    seen: list[tuple[str | None, str | None]] = []

    def fake(params: dict[str, Any], progress: Any) -> dict[str, Any]:
        progress("evaluate", "候補を絞り込み中（10 通り → 2 通り）")
        row = db_session.get(OptimizationJob, job.id)
        assert row is not None
        seen.append((row.stage, row.detail))
        return {"games": params["games"]}

    assert run_once(_opener(db_session), {"fake": fake}) is True
    db_session.refresh(job)
    assert seen == [("evaluate", "候補を絞り込み中（10 通り → 2 通り）")]
    assert job.status == queue.DONE
    assert job.result == {"games": 60}
    assert job.finished_at is not None
    assert run_once(_opener(db_session), {"fake": fake}) is False


def test_worker_records_its_speed_on_the_job_and_the_amount_of_work(db_session: Session) -> None:
    job, _ = queue.enqueue(db_session, "fake", {"games": 60}, strategy="l")

    def fake(params: dict[str, Any], progress: Any) -> dict[str, Any]:
        progress("evaluate", "1 枚目の入れ替えを探しています", WorkStatus(3, 12, 270))
        return {}

    assert run_once(_opener(db_session), {"fake": fake}, games_per_second=42.5) is True
    db_session.refresh(job)
    assert job.games_per_second == 42.5
    assert (job.work_done, job.work_total, job.remaining_seconds) == (3, 12, 270)


def test_remaining_time_counts_down_from_the_last_report(db_session: Session) -> None:
    from datetime import timedelta

    from pocket_api.jobs.estimates import remaining_seconds

    job, _ = queue.enqueue(db_session, "fake", {"games": 60}, strategy="l")
    queue.claim_next(db_session)
    queue.report_progress(db_session, job.id, "evaluate", "探しています", WorkStatus(5, 20, 300))
    db_session.refresh(job)
    reported = job.updated_at
    assert remaining_seconds(job, reported + timedelta(seconds=40)) == 260
    queue.report_progress(db_session, job.id, "evaluate", "探しています", None)
    assert job.remaining_seconds == 300, "仕事の量を出さない報告では前の値を残す"
    assert remaining_seconds(job, reported + timedelta(seconds=400)) == 0
    queue.complete(db_session, job.id, {})
    assert remaining_seconds(job, reported) is None


def test_work_meter_measures_from_the_start_of_the_work() -> None:
    now = [100.0]
    meter = WorkMeter(clock=lambda: now[0])
    assert meter.status() is None
    now[0] = 130.0  # 準備に 30 秒（残り時間に混ぜない）
    meter.begin(10)
    assert meter.status() == WorkStatus(0, 10, None)
    now[0] = 150.0
    meter.advance(2)
    assert meter.status() == WorkStatus(2, 10, 80)
    meter.reach(9)
    meter.advance(5)
    assert meter.status() == WorkStatus(10, 10, 0)


def test_worker_records_failures(db_session: Session) -> None:
    job, _ = queue.enqueue(db_session, "fake", {}, strategy="l")

    def broken(params: dict[str, Any], progress: Any) -> dict[str, Any]:
        raise ValueError("指定のカードで組める種のデッキを作れませんでした")

    run_once(_opener(db_session), {"fake": broken})
    db_session.refresh(job)
    assert job.status == queue.FAILED
    assert job.error is not None
    assert job.error.startswith("指定のカード")


# --- 中身（重い探索は差し替える） ----------------------------------------


@pytest.fixture
def snapshot_id(db_session: Session) -> str:
    snapshot = import_snapshot(db_session, SNAPSHOT)
    db_session.flush()
    return str(snapshot.id)


def test_improve_runner_reports_the_swaps_and_the_rechecked_difference(
    db_session: Session, snapshot_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    base = DeckRecipe.from_text(SAMPLE)
    step1 = base.with_change("A2b 111", -1).with_change("A1 225", 1)
    final = step1.with_change("A3 149", -1).with_change("A2 150", 1)

    def fake(decklist: str, meta: Any, **options: Any) -> RefineReport:
        captured.update(options, meta=meta)
        names = [name for name, _, _ in meta]
        return RefineReport(
            base=base,
            final=final,
            swaps=(Swap(1, "A2b 111", "A1 225"), Swap(2, "A3 149", "A2 150")),
            base_evaluation=Evaluation(0.30, tuple((n, 0.30) for n in names), games=200),
            final_evaluation=Evaluation(0.34, tuple((n, 0.34) for n in names), games=200),
            evaluated=40,
            model_note="",
        )

    monkeypatch.setattr(runners, "refine_deck", fake)
    result = runners.run_improve(
        {"decklist": SAMPLE, "games": 200, "excluded": ["A1 001"], "snapshot_id": snapshot_id},
        lambda *_: None,
        open_session=_opener(db_session),
    )
    assert captured["excluded"] == {"A1 001"}
    assert captured["settings"].games == 60
    assert captured["settings"].report_games == 200
    assert captured["settings"].max_steps == 5
    assert len(captured["meta"]) == 10
    assert result["method"] == "climb"
    assert [s["in_card"]["id"] for s in result["swaps"]] == ["A1 225", "A2 150"]
    assert result["delta"] == pytest.approx(0.04)
    assert result["verdict"] == "clear"
    assert result["precision"] == {"strategy": "l", "games": 200, "noise": pytest.approx(0.01)}
    low, high = result["interval"]
    assert low < 0.34 < high
    assert result["per_opponent"][0]["base"] == pytest.approx(0.30)
    assert result["per_opponent"][0]["final"] == pytest.approx(0.34)
    assert result["final_deck"]["size"] == 20


def test_optimize_runner_passes_the_axis_and_reports_rechecked_rates(
    db_session: Session, snapshot_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    recipe = DeckRecipe.from_text(SAMPLE)

    def fake(decks: Any, **options: Any) -> ProposalReport:
        captured.update(options)
        evaluation = Evaluation(win_rate=0.55, per_opponent=((decks[0].name, 0.55),), games=200)
        return ProposalReport(
            ranked=((recipe, evaluation),),
            searched_win_rate=0.61,
            climb_steps=1,
            pool_size=100,
            evaluated_matchups=10,
            cache_hits=0,
            elapsed_seconds=1.0,
            model_note="",
        )

    monkeypatch.setattr(runners, "propose", fake)
    result = runners.run_optimize(
        {
            "games": 200,
            "target": "Mega Lucario ex Lucario",
            "required": {"A1 096": 2},
            "excluded": [],
            "snapshot_id": snapshot_id,
        },
        lambda *_: None,
        open_session=_opener(db_session),
    )
    assert captured["required"] == {"A1 096": 2}
    assert captured["settings"].games == 60
    assert captured["settings"].report_games == 200
    assert captured["settings"].population == 12
    proposal = result["proposals"][0]
    assert proposal["win_rate"] == pytest.approx(0.55)
    low, high = proposal["interval"]
    assert low < 0.55 < high
    assert result["axis"][0]["id"] == "A1 096"


# --- API -----------------------------------------------------------------


@pytest.fixture
def seeded(snapshot_id: str, api: TestClient) -> TestClient:
    return api


def test_register_improve_returns_a_queued_job_with_an_estimate(seeded: TestClient) -> None:
    response = seeded.post("/jobs/improve", json={"decklist": SAMPLE, "games": 200})
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "queued"
    assert job["estimate_seconds"] == 630
    assert job["params"]["method"] == "climb"
    assert job["reused"] is False

    again = seeded.post("/jobs/improve", json={"decklist": SAMPLE, "games": 200}).json()
    assert again["id"] == job["id"]
    assert again["reused"] is True

    status = seeded.get(f"/jobs/{job['id']}").json()
    assert status["status"] == "queued"
    assert seeded.get(f"/jobs/{job['id']}/result").status_code == 409


def test_register_improve_refuses_invalid_decks(seeded: TestClient) -> None:
    response = seeded.post("/jobs/improve", json={"decklist": "Energy: Water\n2 A1 001"})
    assert response.status_code == 422


def test_result_is_returned_once_done(seeded: TestClient, db_session: Session) -> None:
    job = seeded.post("/jobs/improve", json={"decklist": SAMPLE, "games": 60}).json()
    import uuid

    queue.complete(db_session, uuid.UUID(job["id"]), {"base_win_rate": 0.3})
    body = seeded.get(f"/jobs/{job['id']}/result").json()
    assert body["result"] == {"base_win_rate": 0.3}
    assert body["job"]["status"] == "done"


def test_register_optimize_resolves_the_target_and_checks_the_axis(seeded: TestClient) -> None:
    ok = seeded.post(
        "/jobs/optimize",
        json={
            "target": "mega-lucario-ex-b3-lucario-a2",
            "axis": [{"id": "A1 096", "count": 2}],
        },
    )
    assert ok.status_code == 200
    params = ok.json()["params"]
    assert params["target"] == "Mega Lucario ex Lucario"
    assert params["required"] == {"A1 096": 2}
    assert ok.json()["estimate_seconds"] == 600

    assert seeded.post("/jobs/optimize", json={"target": "nope"}).status_code == 422
    assert seeded.post("/jobs/optimize", json={"axis": [{"id": "Z9 999"}]}).status_code == 422


def test_unknown_jobs_are_404(seeded: TestClient) -> None:
    assert seeded.get("/jobs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_fast_optimize_shrinks_the_search_not_just_the_recheck() -> None:
    """時間のほとんどは探索中の対戦。測り直しの試合数だけ減らしても速くならない。"""
    fast, normal, precise = (runners.optimize_settings(g) for g in (60, 200, 800))
    assert fast.population * fast.generations < normal.population * normal.generations
    assert fast.climb_steps < normal.climb_steps
    assert (fast.report_games, normal.report_games, precise.report_games) == (60, 200, 800)
    assert precise.games == 200


def test_estimates_come_from_finished_jobs_once_there_are_any(
    db_session: Session, seeded: TestClient
) -> None:
    from datetime import UTC, datetime, timedelta

    before = seeded.get("/jobs/estimates").json()
    assert before["optimize"]["60"] == {"seconds": 300, "samples": 0, "basis": "default"}

    start = datetime(2026, 9, 13, tzinfo=UTC)
    for minutes in (8, 10, 30):
        job, _ = queue.enqueue(db_session, "optimize", {"games": 60, "n": minutes}, strategy="l")
        job.status = queue.DONE
        job.started_at = start
        job.finished_at = start + timedelta(minutes=minutes)
    db_session.flush()

    after = seeded.get("/jobs/estimates").json()
    assert after["optimize"]["60"] == {"seconds": 600, "samples": 3, "basis": "history"}
    assert after["optimize"]["200"]["samples"] == 0
    registered = seeded.post("/jobs/optimize", json={"games": 60}).json()
    assert registered["estimate_seconds"] == 600
    assert registered["estimate_samples"] == 3


def test_estimates_follow_the_speed_the_worker_measured(
    db_session: Session, seeded: TestClient
) -> None:
    """CPU の少ないマシンでは、定数の目安をワーカーの速さで換算する。記録も速さで直して使う。"""
    from datetime import UTC, datetime, timedelta

    from pocket_api.jobs.estimates import REFERENCE_SPEED
    from pocket_api.jobs.speed import record_speed

    record_speed(db_session, REFERENCE_SPEED / 4)  # 定数を測ったマシンの 4 分の 1 の速さ
    body = seeded.get("/jobs/estimates").json()
    assert body["improve"]["200"] == {"seconds": 630 * 4, "samples": 0, "basis": "speed"}
    assert body["diagnose"]["200"]["basis"] == "speed"
    assert body["diagnose"]["200"]["seconds"] > body["diagnose"]["60"]["seconds"]

    # 2 倍速いワーカーで 10 分かかった記録は、いまのワーカーでは 20 分
    start = datetime(2026, 9, 14, tzinfo=UTC)
    job, _ = queue.enqueue(db_session, "optimize", {"games": 60, "n": 1}, strategy="l")
    job.status = queue.DONE
    job.started_at = start
    job.finished_at = start + timedelta(minutes=10)
    job.games_per_second = REFERENCE_SPEED / 2
    db_session.flush()
    after = seeded.get("/jobs/estimates").json()
    assert after["optimize"]["60"] == {"seconds": 1200, "samples": 1, "basis": "history"}


def test_measures_the_speed_of_the_engine() -> None:
    from pocket_api.jobs.speed import measure_speed

    assert measure_speed(min_seconds=0.01, snapshot=SNAPSHOT) > 0
