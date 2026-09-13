"""デッキの改善（何枚でも入れ替える）のテスト。

探索は重いので、方策 p・少ない試合数・学習データなしで流れだけを確かめる。
"""

from pathlib import Path

from pocket_api.optimize.diagnose import load_meta_decks
from pocket_api.optimize.progress import WorkMeter
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.refine import RefineSettings, refine_deck

SAMPLE = Path("/workspace/data/decks/mydeck_sample001.txt").read_text(encoding="utf-8")
SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")


def test_climbs_from_the_deck_and_rechecks_before_and_after(tmp_path: Path) -> None:
    stages: list[str] = []
    settings = RefineSettings(
        strategy="p",
        games=20,
        report_games=20,
        max_steps=2,
        neighbours=4,
        similar_width=4,
        store=tmp_path / "none.jsonl",  # 学習データなし（候補は neighbours 件まで実評価）
    )
    report = refine_deck(
        SAMPLE,
        load_meta_decks(SNAPSHOT)[:3],
        settings=settings,
        progress=lambda stage, _detail: stages.append(stage),
    )
    assert report.base == DeckRecipe.from_text(SAMPLE)
    assert len(report.swaps) <= 2
    assert [s.step for s in report.swaps] == list(range(1, len(report.swaps) + 1))

    # 入れ替えを順に当てると改善後のデッキになる
    replayed = report.base
    for swap in report.swaps:
        replayed = replayed.with_change(swap.out_card, -1).with_change(swap.in_card, 1)
    assert replayed == report.final

    assert report.base_evaluation.games == 20
    assert report.final_evaluation.games == 20
    if not report.swaps:
        assert report.final_evaluation == report.base_evaluation
    assert stages[0] == "prepare"
    assert stages[-1] == "finalize"


def test_counts_the_work_so_the_remaining_time_can_be_shown(tmp_path: Path) -> None:
    """仕事の量（デッキの評価 1 回を 1）を数え、見込みの上限を超えずに最後は上限に届く。"""
    settings = RefineSettings(
        strategy="p",
        games=20,
        report_games=40,
        max_steps=2,
        neighbours=4,
        similar_width=4,
        store=tmp_path / "none.jsonl",
    )
    meter = WorkMeter()
    seen: list[tuple[int, int] | None] = []
    refine_deck(
        SAMPLE,
        load_meta_decks(SNAPSHOT)[:3],
        settings=settings,
        progress=lambda _stage, _detail: seen.append(
            (meter.done, meter.total) if meter.total else None
        ),
        meter=meter,
    )
    # 元のデッキ 1 + 2 手 × 4 件 + 測り直し（40 / 20 = 2）× 2 デッキ
    assert meter.total == 1 + 2 * 4 + 2 * 2
    assert meter.done == meter.total
    progressed = [s for s in seen if s is not None]
    assert progressed, "探索中に仕事の量を出す"
    assert all(done <= total for done, total in progressed)
    assert [d for d, _ in progressed] == sorted(d for d, _ in progressed)
