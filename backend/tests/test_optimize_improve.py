"""改善提案のテスト。

自分のデッキに対して「どのカードを入れ替えると何%上がるか」を示す。
候補はモデルで足切りし、残りを実評価して並べる（`docs/status.md` 3.14）。
"""

from pathlib import Path

import pytest

from pocket_api.optimize.improve import Improvement, suggest_improvements
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.search import full_card_pool, neighbours

SAMPLE = Path("/workspace/data/decks/mydeck_sample002.txt")
SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")


def test_suggests_swaps_with_their_effect() -> None:
    result = suggest_improvements(
        SAMPLE.read_text(encoding="utf-8"),
        SNAPSHOT,
        strategy="p",
        games=20,
        candidates=4,
        neighbours_limit=12,
    )
    assert result.base_win_rate >= 0.0
    for item in result.suggestions:
        assert isinstance(item, Improvement)
        assert item.out_card
        assert item.in_card
        # 入れ替え後の勝率と、元との差
        assert item.win_rate == pytest.approx(result.base_win_rate + item.delta, abs=1e-9)


def test_sorts_suggestions_by_improvement() -> None:
    result = suggest_improvements(
        SAMPLE.read_text(encoding="utf-8"),
        SNAPSHOT,
        strategy="p",
        games=20,
        candidates=4,
        neighbours_limit=12,
    )
    deltas = [item.delta for item in result.suggestions]
    assert deltas == sorted(deltas, reverse=True), "効果の大きい順に並べる"


def test_screens_every_candidate_by_default() -> None:
    """既定では候補を間引かない。

    モデルで並べ替えるのは速いので、間引いても実評価の回数は変わらない。
    当たりの候補を取りこぼすだけだった（docs/analysis/card-pool-expansion.md）。
    """
    text = SAMPLE.read_text(encoding="utf-8")
    base = DeckRecipe.from_text(text)
    every = neighbours(base, full_card_pool(base.energy), similar_width=12)
    assert len(every) > 60, "間引きが効く大きさでないと確かめられない"
    result = suggest_improvements(
        text,
        SNAPSHOT,
        strategy="p",
        games=20,
        candidates=1,
        similar_width=12,
    )
    assert result.screened == len(every)


def test_keeps_the_requested_number_of_candidates() -> None:
    result = suggest_improvements(
        SAMPLE.read_text(encoding="utf-8"),
        SNAPSHOT,
        strategy="p",
        games=20,
        candidates=3,
        neighbours_limit=10,
    )
    assert len(result.suggestions) <= 3


def test_rejects_a_deck_with_unimplemented_cards() -> None:
    # カードは全て実装済みになったので、実在のカードではこの経路を通せない
    # （docs/deckgym-fork.md）。存在しないカード ID が同じ扱いになるので代用する。
    text = """\
Energy: Water
2 A1 053
2 A1 054
2 A1 055
2 A2 150
2 A1 220
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 A1 999
"""
    with pytest.raises(ValueError, match="未実装"):
        suggest_improvements(text, SNAPSHOT, strategy="p", games=20)


def test_respects_ownership() -> None:
    from pocket_api.optimize.recipe import Ownership

    text = SAMPLE.read_text(encoding="utf-8")
    owned = Ownership({card_id: 2 for card_id, _ in DeckRecipe.from_text(text).cards})
    result = suggest_improvements(
        text,
        SNAPSHOT,
        strategy="p",
        games=20,
        candidates=3,
        neighbours_limit=10,
        ownership=owned,
    )
    # 持っている札の範囲でしか提案しない。
    # デッキ内の枚数配分を変える入れ替え（1 枚のカードを 2 枚目にする）は成り立つ
    deck_ids = {card_id for card_id, _ in DeckRecipe.from_text(text).cards}
    for item in result.suggestions:
        swapped = DeckRecipe.from_text(item.decklist)
        assert {card_id for card_id, _ in swapped.cards} <= deck_ids, "持っていない札は提案しない"
        for card_id, count in swapped.cards:
            assert count <= owned.owned(card_id), "所持数を超えない"


def test_render_marks_a_difference_within_noise_as_uncertain() -> None:
    from pocket_api.optimize.improve import ImprovementReport
    from pocket_api.optimize.improve_cli import render

    report = ImprovementReport(
        base_win_rate=0.30,
        suggestions=(
            Improvement(
                out_card="Sabrina",
                in_card="Cyrus",
                win_rate=0.31,
                delta=0.01,
                decklist="Energy: Water\n2 A1 053\n",
            ),
        ),
        evaluated=1,
        screened=10,
    )
    text = render(report, "見本", games=200, strategy="l")
    assert "30.0%" in text
    assert "+1.0%" in text
    assert "言い切れない" in text, "評価ぶれの範囲なら断定しない"


def test_render_recommends_a_swap_that_clears_the_noise() -> None:
    from pocket_api.optimize.improve import ImprovementReport
    from pocket_api.optimize.improve_cli import render

    report = ImprovementReport(
        base_win_rate=0.30,
        suggestions=(
            Improvement(
                out_card="Sabrina",
                in_card="Cyrus",
                win_rate=0.45,
                delta=0.15,
                decklist="Energy: Water\n2 A1 053\n",
            ),
        ),
        evaluated=1,
        screened=10,
    )
    text = render(report, "見本", games=200, strategy="l")
    assert "+15.0%" in text
    assert "デッキリスト" in text, "採用できる形で出す"


def test_screening_is_not_capped_per_slot() -> None:
    """抜く札ごとの上限で、実評価に回る前の候補を削らない。

    予測は「どの札を抜くか」でほぼ決まるので、抜く札ごとに上位 2 件へ絞ると
    候補が「札の種類数 × 2」で頭打ちになり、当たりがその外に落ちる。
    実測では、この上限だけで到達できる勝率が 36.7% → 32.2% に下がっていた
    （`docs/analysis/card-pool-expansion.md`）。散らすのは**見せる側**でやる。
    """
    text = SAMPLE.read_text(encoding="utf-8")
    base = DeckRecipe.from_text(text)
    every = neighbours(base, full_card_pool(base.energy), similar_width=12)
    slots = len({card_id for card_id, _ in base.cards})
    assert len(every) > slots * 2, "上限が効く大きさでないと確かめられない"
    # モデルがある経路を通す（方策 l には学習データがある）
    result = suggest_improvements(
        text,
        SNAPSHOT,
        strategy="l",
        games=20,
        candidates=slots * 2 + 4,
        similar_width=12,
    )
    assert result.model_note, "モデルの説明がない"
    assert "使わない" not in result.model_note, result.model_note
    assert result.evaluated == slots * 2 + 4


def test_diversify_spreads_the_candidates_over_slots() -> None:
    """同じ札を抜く案ばかり並べても、選択肢を示したことにならない。"""
    from pocket_api.optimize.improve import diversify

    base = DeckRecipe.from_text(SAMPLE.read_text(encoding="utf-8"))
    out_ids = [card_id for card_id, _ in base.cards]
    # 同じ 1 枚（out_ids[0]）を抜く案を 5 通り、別の 1 枚を抜く案を 2 通り作る
    pool = ["A1 219", "A1 220", "A1 223", "A1 225", "A1 226", "A2 150", "A2 155"]
    same_slot = [base.with_change(out_ids[0], -1).with_change(cid, 1) for cid in pool[:5]]
    other_slot = [base.with_change(out_ids[1], -1).with_change(cid, 1) for cid in pool[5:]]

    kept = diversify(same_slot + other_slot, base, lambda recipe: 0.5, per_slot=2)
    swapped_out = [
        next(cid for cid, n in base.cards if n > dict(recipe.cards).get(cid, 0)) for recipe in kept
    ]
    assert swapped_out.count(out_ids[0]) <= 2, "同じ札を抜く案は上限まで"
    assert out_ids[1] in swapped_out, "別の札を抜く案も残す"


def test_diversify_keeps_the_predicted_best_of_each_slot() -> None:
    from pocket_api.optimize.improve import diversify

    base = DeckRecipe.from_text(SAMPLE.read_text(encoding="utf-8"))
    out_id = base.cards[0][0]
    candidates = [base.with_change(out_id, -1).with_change(cid, 1) for cid in ("A1 219", "A1 220")]
    scores = {candidates[0].cards: 0.1, candidates[1].cards: 0.9}

    kept = diversify(candidates, base, lambda recipe: scores[recipe.cards], per_slot=1)
    assert [r.cards for r in kept] == [candidates[1].cards], "予測の高いほうを残す"


def test_delta_noise_shrinks_with_more_games() -> None:
    """ぶれは試合数の平方根に反比例する。4 倍回せば半分になる。"""
    from pocket_api.optimize.improve_cli import delta_noise

    assert delta_noise(200) == pytest.approx(0.010)
    assert delta_noise(800) == pytest.approx(0.005)


def test_never_suggests_an_excluded_card_and_reports_progress() -> None:
    """「持っていない」と言われたカードは入れる候補にしない（F-10）。"""
    text = SAMPLE.read_text(encoding="utf-8")
    first = suggest_improvements(
        text, SNAPSHOT, strategy="p", games=20, candidates=3, neighbours_limit=10
    )
    assert first.suggestions
    banned = first.suggestions[0].in_card_id
    assert banned
    stages: list[str] = []
    again = suggest_improvements(
        text,
        SNAPSHOT,
        strategy="p",
        games=20,
        candidates=3,
        neighbours_limit=10,
        excluded={banned},
        progress=lambda stage, _detail: stages.append(stage),
    )
    assert banned not in {item.in_card_id for item in again.suggestions}
    assert stages[0] == "prepare"
    assert "evaluate" in stages
    assert stages[-1] == "finalize"


@pytest.mark.parametrize(
    ("delta", "verdict"),
    [(0.038, "clear"), (0.015, "likely"), (0.002, "none"), (-0.002, "none"), (-0.03, "worse")],
)
def test_judges_a_difference_against_the_noise(delta: float, verdict: str) -> None:
    """ぶれ ±1.0 点（200 試合）と比べた判定。画面はこの判定で見た目を変える。"""
    from pocket_api.optimize.improve_cli import judge_delta

    assert judge_delta(delta, 200) == verdict
