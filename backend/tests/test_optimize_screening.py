"""二段構えの評価のテスト。

モデルで当たりを付け、上位だけ実シミュレーションで裏を取る。
モデルの予測だけで決めると「モデルが高く評価するが実際は弱いデッキ」を掴む
（`docs/adr/0004-surrogate-model.md`）。
"""

import pytest

from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.screening import screen

FIRE = """\
Energy: Fire
2 A1 042
2 A1 043
2 A1 044
2 A1 049
2 A1 050
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
"""

WATER = """\
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
2 P-A 007
"""


def test_screen_evaluates_only_the_top_candidates() -> None:
    """予測の上位だけを実評価する。"""
    candidates = [DeckRecipe.from_text(FIRE), DeckRecipe.from_text(WATER)]
    calls: list[DeckRecipe] = []

    def predict(recipe: DeckRecipe) -> float:
        # 炎のほうが良いと予測する
        return 0.9 if recipe.energy == ("Fire",) else 0.1

    def evaluate(recipe: DeckRecipe) -> float:
        calls.append(recipe)
        return 0.5

    result = screen(candidates, predict, evaluate, keep=1)
    assert len(calls) == 1, "実評価は上位 1 件だけのはず"
    assert calls[0].energy == ("Fire",)
    assert result[0][0].energy == ("Fire",)
    assert result[0][1] == pytest.approx(0.5), "返すのは実評価の値"


def test_screen_keeps_the_requested_number() -> None:
    candidates = [DeckRecipe.from_text(FIRE), DeckRecipe.from_text(WATER)]
    result = screen(candidates, lambda _: 0.5, lambda _: 0.5, keep=2)
    assert len(result) == 2


def test_screen_sorts_by_the_real_evaluation() -> None:
    """並び順は予測ではなく実評価で決める。"""
    candidates = [DeckRecipe.from_text(FIRE), DeckRecipe.from_text(WATER)]

    def predict(recipe: DeckRecipe) -> float:
        return 0.9 if recipe.energy == ("Fire",) else 0.8

    def evaluate(recipe: DeckRecipe) -> float:
        # 実際には水のほうが強い（予測が外れた場合）
        return 0.3 if recipe.energy == ("Fire",) else 0.7

    result = screen(candidates, predict, evaluate, keep=2)
    assert result[0][0].energy == ("Water",), "実評価の高い順に並ぶ"


def test_screen_handles_fewer_candidates_than_keep() -> None:
    candidates = [DeckRecipe.from_text(FIRE)]
    result = screen(candidates, lambda _: 0.5, lambda _: 0.5, keep=10)
    assert len(result) == 1


def test_screen_returns_nothing_for_no_candidates() -> None:
    assert screen([], lambda _: 0.5, lambda _: 0.5, keep=3) == ()
