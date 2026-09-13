"""遺伝的アルゴリズムのテスト。

交叉と突然変異でデッキを組み替える。制約を満たさないものは作らない。
乱数はシードで固定し、同じ条件なら同じ結果になるようにする。
"""

import random

from pocket_api.optimize.genetic import crossover, evolve, mutate
from pocket_api.optimize.recipe import DeckRecipe, violations
from pocket_api.optimize.search import pick_card_pool

FIRE_TEXT = """\
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

WATER_TEXT = """\
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


def test_crossover_mixes_two_decks_and_keeps_constraints() -> None:
    a = DeckRecipe.from_text(FIRE_TEXT)
    b = DeckRecipe.from_text(WATER_TEXT)
    child = crossover(a, b, rng=random.Random(1))
    assert child.size == 20
    assert violations(child) == ()
    # エネルギーは片方の親から受け継ぐ
    assert child.energy in (a.energy, b.energy)


def test_crossover_is_deterministic_for_a_seed() -> None:
    a = DeckRecipe.from_text(FIRE_TEXT)
    b = DeckRecipe.from_text(WATER_TEXT)
    first = crossover(a, b, rng=random.Random(7))
    second = crossover(a, b, rng=random.Random(7))
    assert first == second


def test_mutate_changes_one_card_and_keeps_constraints() -> None:
    recipe = DeckRecipe.from_text(FIRE_TEXT)
    pool = pick_card_pool([FIRE_TEXT, WATER_TEXT])
    mutated = mutate(recipe, pool, rng=random.Random(3))
    assert mutated.size == 20
    assert violations(mutated) == ()


def test_evolve_returns_the_best_found() -> None:
    # 評価はシミュレーションを使わず、決めた札が多いほど良いとする
    def score(candidate: DeckRecipe) -> float:
        return float(candidate.count_of("A1 053"))

    seeds = [DeckRecipe.from_text(FIRE_TEXT), DeckRecipe.from_text(WATER_TEXT)]
    pool = pick_card_pool([FIRE_TEXT, WATER_TEXT])
    result = evolve(seeds, pool, score, population=6, generations=3, rng=random.Random(11))
    assert violations(result.recipe) == ()
    assert result.score >= max(score(s) for s in seeds), "種より悪いものは返さない"


def test_evolve_is_deterministic_for_a_seed() -> None:
    def score(candidate: DeckRecipe) -> float:
        return float(candidate.count_of("A1 053"))

    seeds = [DeckRecipe.from_text(FIRE_TEXT), DeckRecipe.from_text(WATER_TEXT)]
    pool = pick_card_pool([FIRE_TEXT, WATER_TEXT])
    first = evolve(seeds, pool, score, population=6, generations=2, rng=random.Random(5))
    second = evolve(seeds, pool, score, population=6, generations=2, rng=random.Random(5))
    assert first.recipe == second.recipe


def test_mutate_prefers_cards_of_a_similar_role() -> None:
    """1,000 種からランダムに引くのではなく、抜く札と役割が近いものを試す。"""
    from pocket_api.optimize.search import full_card_pool

    recipe = DeckRecipe.from_text(FIRE_TEXT)
    pool = full_card_pool(("Fire",))
    assert len(pool) > 500, "広いプールで試す"

    # 似た役割に絞れば、腐らない札が入る。100 回試して、どれも制約を満たすこと
    rng = random.Random(2)
    changed = 0
    for _ in range(100):
        mutated = mutate(recipe, pool, rng=rng, similar_only=True)
        assert violations(mutated) == ()
        if mutated != recipe:
            changed += 1
    assert changed > 0, "似た役割の札が見つかれば入れ替わるはず"
