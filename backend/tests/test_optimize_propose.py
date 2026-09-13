"""デッキ提案（F-03 / F-05 / F-09）のテスト。

探索は重いので、方策 p・少ない試合数・小さい集団で流れだけを確かめる。
"""

from pathlib import Path

from pocket_api.optimize.cli import load_meta
from pocket_api.optimize.constraints import DeckConstraints
from pocket_api.optimize.progress import WorkMeter
from pocket_api.optimize.propose import SearchSettings, propose

SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")
PIKACHU_EX = "A1 096"

TINY = SearchSettings(
    strategy="p",
    games=4,
    report_games=4,
    population=3,
    generations=1,
    climb_steps=1,
    neighbours=3,
    similar_width=4,
    pool="meta",
    top=2,
    use_surrogate=False,
)


def test_every_proposal_contains_the_axis_card_and_reports_progress(tmp_path: Path) -> None:
    decks = load_meta(SNAPSHOT)[:3]
    stages: list[str] = []
    report = propose(
        decks,
        settings=TINY,
        target=decks[0].name,
        required={PIKACHU_EX: 2},
        progress=lambda stage, _detail: stages.append(stage),
    )
    assert 1 <= len(report.ranked) <= 2
    constraints = DeckConstraints.of({PIKACHU_EX: 2})
    for recipe, evaluation in report.ranked:
        assert constraints.violations(recipe) == ()
        assert [name for name, _ in evaluation.per_opponent] == [decks[0].name]
        assert evaluation.games == TINY.report_games
    assert stages[0] == "prepare"
    assert "evaluate" in stages
    assert stages[-1] == "finalize"
    rates = [evaluation.win_rate for _, evaluation in report.ranked]
    assert rates == sorted(rates, reverse=True)


def test_counts_the_work_of_the_search() -> None:
    decks = load_meta(SNAPSHOT)[:3]
    meter = WorkMeter()
    propose(decks, settings=TINY, target=decks[0].name, meter=meter)
    # 集団 3 ×（世代 1 + 1）+ 山登り（1 + 1 手 × 3 件）+ 測り直し 2 件 ×（4 / 4）
    assert meter.total == 3 * 2 + (1 + 1 * 3) + 2 * 1
    assert meter.done == meter.total
