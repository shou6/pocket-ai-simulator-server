"""二段構えの評価。

サロゲートモデルで当たりを付け、上位だけ実シミュレーションで裏を取る
（`docs/adr/0004-surrogate-model.md`）。

モデルの予測誤差は 6.3% で、実シミュレーション 200 試合のぶれ（±7%）と同水準だが、
**予測だけで決めてはいけない**。学習データはメタデッキとその変異に偏っているので、
まったく別系統のデッキでは外す。上位を実評価することで、その取りこぼしを防ぐ。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pocket_api.optimize.recipe import DeckRecipe


def screen(
    candidates: Sequence[DeckRecipe],
    predict: Callable[[DeckRecipe], float],
    evaluate: Callable[[DeckRecipe], float],
    *,
    keep: int,
) -> tuple[tuple[DeckRecipe, float], ...]:
    """予測で絞り、残ったものだけ実評価する。実評価の高い順に返す。

    # 引数

    - `predict`：モデルによる予測。速いが外すことがある
    - `evaluate`：実シミュレーション。遅いが正しい
    - `keep`：実評価に回す件数
    """
    if not candidates:
        return ()
    ranked = sorted(candidates, key=predict, reverse=True)
    survivors = ranked[: max(1, keep)]
    scored = [(recipe, evaluate(recipe)) for recipe in survivors]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return tuple(scored)
