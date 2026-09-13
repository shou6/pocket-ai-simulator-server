"""遺伝的アルゴリズム。

`docs/requirements.md` 8 章の「遺伝的アルゴリズムと局所探索の併用」のうち、
組み替えの部分。初期集団はメタデッキとその派生で、ランダム生成には頼らない。

乱数はすべて呼び出し側から渡す `random.Random` を使う。同じシードなら同じ結果になり、
シミュレーションの決定性（`docs/status.md`）と揃う。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pocket_api.optimize.recipe import DECK_SIZE, DeckRecipe, Ownership, violations
from pocket_api.optimize.search import evolution_is_playable, type_matches
from pocket_api.optimize.similar import similar_cards

if TYPE_CHECKING:
    from pocket_api.optimize.constraints import DeckConstraints

MAX_REPAIR_TRIES = 200
"""制約を満たすまでに試す回数の上限。超えたら諦めて親を返す。"""


def crossover(
    a: DeckRecipe,
    b: DeckRecipe,
    *,
    rng: random.Random,
    constraints: DeckConstraints | None = None,
) -> DeckRecipe:
    """2 つのデッキを混ぜる。

    両親のカードを合わせた袋から 20 枚を引き直す。よく使われている札
    （両親に多く入っている札）ほど残りやすい。制約を満たすまで引き直す。

    軸のカード（`constraints.required`）は袋から引かずに先に置く。引き直しに任せると
    軸が全部残る確率が低く、200 回試しても制約を満たせないことが多い。
    """
    energy = a.energy if rng.random() < 0.5 else b.energy
    bag: list[str] = []
    for recipe in (a, b):
        for card_id, count in recipe.cards:
            bag.extend([card_id] * count)
    fixed: list[str] = []
    for card_id, count in constraints.required if constraints is not None else ():
        for _ in range(count):
            fixed.append(card_id)
            # 両親とも軸を持つので、袋からは 2 人ぶん抜いておく
            for _parent in range(2):
                if card_id in bag:
                    bag.remove(card_id)

    for _ in range(MAX_REPAIR_TRIES):
        child = _draw(bag, energy, rng, fixed=fixed)
        if child is None or violations(child):
            continue
        if constraints is not None and constraints.violations(child):
            continue
        return child
    return a


def _draw(
    bag: list[str], energy: tuple[str, ...], rng: random.Random, *, fixed: list[str] | None = None
) -> DeckRecipe | None:
    """袋から 20 枚を引いてデッキにする。同名の上限は引く段階では見ない。"""
    placed = list(fixed or [])
    if len(bag) < DECK_SIZE - len(placed):
        return None
    picked = placed + rng.sample(bag, DECK_SIZE - len(placed))
    counts: dict[str, int] = {}
    for card_id in picked:
        counts[card_id] = counts.get(card_id, 0) + 1
    return DeckRecipe(cards=tuple(sorted(counts.items())), energy=energy)


def mutate(
    recipe: DeckRecipe,
    pool: tuple[str, ...],
    *,
    rng: random.Random,
    ownership: Ownership | None = None,
    similar_only: bool = True,
    similar_width: int = 12,
    constraints: DeckConstraints | None = None,
) -> DeckRecipe:
    """1 枚だけ入れ替える。制約を満たす入れ替えが見つからなければ元のまま返す。

    `similar_only` なら、抜く札と役割が近いものから選ぶ。プールが 1,000 種あると
    ランダムに引いても腐る札ばかりになるので、既定で有効にする。
    """
    if not recipe.cards or not pool:
        return recipe
    for _ in range(MAX_REPAIR_TRIES):
        out_id = rng.choice([card_id for card_id, _ in recipe.cards])
        if similar_only:
            neighbours_of_out = similar_cards(out_id, limit=similar_width, pool=pool)
            if not neighbours_of_out:
                continue
            in_id = rng.choice([cid for cid, _ in neighbours_of_out])
        else:
            in_id = rng.choice(pool)
        if in_id == out_id:
            continue
        candidate = recipe.with_change(out_id, -1).with_change(in_id, 1)
        if violations(candidate, ownership=ownership):
            continue
        if constraints is not None and constraints.violations(candidate):
            continue
        # デッキのタイプで動かない札や、進化元のない進化カードは入れても腐る
        if not type_matches(candidate, in_id) or not evolution_is_playable(candidate, in_id):
            continue
        return candidate
    return recipe


@dataclass(frozen=True)
class EvolveResult:
    """探索の結果。"""

    recipe: DeckRecipe
    """見つかった最良のデッキ。"""

    score: float
    """その評価値。"""

    evaluated: int
    """評価した回数。"""


def evolve(
    seeds: list[DeckRecipe],
    pool: tuple[str, ...],
    score: Callable[[DeckRecipe], float],
    *,
    population: int = 20,
    generations: int = 10,
    rng: random.Random | None = None,
    ownership: Ownership | None = None,
    constraints: DeckConstraints | None = None,
) -> EvolveResult:
    """種のデッキから始めて、交叉と突然変異で候補を広げる。

    各世代で上位半分を残し、そこから子を作る。評価が重いので、
    同じデッキを二度評価しないよう覚えておく。
    """
    if not seeds:
        raise ValueError("種のデッキがありません")
    random_source = rng if rng is not None else random.Random(1)

    scored: dict[tuple[tuple[str, int], ...], float] = {}

    def value_of(recipe: DeckRecipe) -> float:
        cached = scored.get(recipe.cards)
        if cached is None:
            cached = score(recipe)
            scored[recipe.cards] = cached
        return cached

    current = list(seeds)
    while len(current) < population:
        parent = random_source.choice(seeds)
        current.append(
            mutate(parent, pool, rng=random_source, ownership=ownership, constraints=constraints)
        )

    for _ in range(generations):
        ranked = sorted(current, key=value_of, reverse=True)
        survivors = ranked[: max(2, population // 2)]
        children: list[DeckRecipe] = list(survivors)
        while len(children) < population:
            mother, father = (
                random_source.sample(survivors, 2)
                if len(survivors) >= 2
                else (survivors[0], survivors[0])
            )
            child = crossover(mother, father, rng=random_source, constraints=constraints)
            if random_source.random() < 0.5:
                child = mutate(
                    child, pool, rng=random_source, ownership=ownership, constraints=constraints
                )
            children.append(child)
        current = children

    best = max(current + list(seeds), key=value_of)
    return EvolveResult(recipe=best, score=value_of(best), evaluated=len(scored))
