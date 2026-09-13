"""サロゲートモデルの学習データに使うデッキを組み立てる。

学習データの顔ぶれは、探索が実際に見る候補と揃っていないと役に立たない。
メタデッキを何度も変異させた鎖だけを集めると「ほぼ元のデッキ」と「壊れたデッキ」
しか並ばず、モデルはその 2 つを見分けられれば高い精度に見えてしまう。

一方で山登りが並べ替えるのは **1 枚しか違わない候補どうし**で、勝率の幅は数 %
しかない。そこを見分けられるようにするには、1 枚違いの組を学習データに入れる。
"""

from __future__ import annotations

import random

from pocket_api.optimize.genetic import mutate
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.search import neighbours

MAX_MUTATIONS = 12
"""基準デッキから何回まで変異させるか。

離れすぎたデッキは探索が見ないので、集めても無駄になる。
"""

NEAR_PER_BASE = 2
"""基準 1 つあたり、何枚違いの近傍を取るか。"""

_NEIGHBOUR_LIMIT = 40
"""近傍を作るときに見る数。全部作ると重いので抜き出す。"""

SIMILAR_WIDTH = 16
"""1 枚違いを作るとき、抜く札と役割の近い何枚から選ぶか。

**探索と同じ値にする。** 探索は近い上位 `similar_width` 件しか候補にしないので、
学習データの 1 枚違いがプール全体からの無作為な差し替えだと、モデルは
「別物どうし」しか教わらず、本番で並べる候補を見分けられない
（`docs/analysis/card-pool-expansion.md`）。

基準デッキを作る変異（`mutate`）にも同じ値を渡す。変異の幅が探索と違うと、
鎖でできるデッキの顔ぶれ自体が本番からずれる。
"""


def plan_decks(
    base: DeckRecipe,
    pool: tuple[str, ...],
    count: int,
    *,
    rng: random.Random,
    max_mutations: int = MAX_MUTATIONS,
    near_per_base: int = NEAR_PER_BASE,
    similar_width: int = SIMILAR_WIDTH,
) -> tuple[DeckRecipe, ...]:
    """評価するデッキを `count` 個そろえる。

    変異の回数を変えた基準デッキと、その 1 枚違いを交互に混ぜる。
    同じシードなら同じ顔ぶれになる。
    """
    planned: list[DeckRecipe] = []
    seen: set[tuple[tuple[str, int], ...]] = set()

    def add(recipe: DeckRecipe) -> bool:
        if recipe.cards in seen:
            return False
        seen.add(recipe.cards)
        planned.append(recipe)
        return True

    while len(planned) < count:
        depth = rng.randrange(max_mutations + 1)
        anchor = base
        for _ in range(depth):
            anchor = mutate(anchor, pool, rng=rng, similar_width=similar_width)
        if not add(anchor) or len(planned) >= count:
            continue
        nearby = neighbours(
            anchor, pool, limit=_NEIGHBOUR_LIMIT, rng=rng, similar_width=similar_width
        )
        for near in nearby[:near_per_base]:
            if len(planned) >= count:
                break
            add(near)
    return tuple(planned[:count])
