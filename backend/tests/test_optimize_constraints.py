"""軸にするカード（F-09）と使わないカード（F-10）の制約のテスト。

シミュレーションは回さない。制約を満たす候補だけが作られることを確かめる。
"""

import random
from functools import cache
from pathlib import Path

import pytest

from pocket_api.optimize.cli import load_meta
from pocket_api.optimize.constraints import (
    DeckConstraints,
    axis_energy,
    build_seed,
    filler_order,
)
from pocket_api.optimize.genetic import crossover, mutate
from pocket_api.optimize.recipe import DeckRecipe, violations
from pocket_api.optimize.search import full_card_pool, neighbours

SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")
LUCARIO = DeckRecipe.from_text(load_meta(SNAPSHOT)[0].decklist)
PIKACHU_EX = "A1 096"
MEGA_BLAZIKEN_EX = "B1 036"
POKE_BALL = "A2b 111"
POKE_BALL_PROMO = "P-A 005"


def test_no_constraints_means_no_violations() -> None:
    assert DeckConstraints().violations(LUCARIO) == ()


def test_reports_a_missing_axis_card() -> None:
    found = DeckConstraints.of({PIKACHU_EX: 2}).violations(LUCARIO)
    assert any("軸のカードが足りません" in message for message in found)


def test_excluded_cards_cover_every_print() -> None:
    """再録違いも同じカードとして外す。"""
    assert LUCARIO.count_of(POKE_BALL_PROMO) > 0
    found = DeckConstraints.of(excluded=[POKE_BALL]).violations(LUCARIO)
    assert any("使わないカード" in message for message in found)


def test_axis_evolution_needs_its_ancestor() -> None:
    constraints = DeckConstraints.of({MEGA_BLAZIKEN_EX: 2})
    without = LUCARIO.with_change("P-A 007", -2).with_change(MEGA_BLAZIKEN_EX, 2)
    assert any("進化元" in message for message in constraints.violations(without))


def test_axis_energy_follows_the_main_attack() -> None:
    assert axis_energy(((PIKACHU_EX, 2),), ("Fighting",)) == ("Lightning",)
    assert axis_energy(((PIKACHU_EX, 2),), ("Lightning", "Metal")) == ("Lightning", "Metal")
    assert axis_energy(((POKE_BALL, 2),), ("Fighting",)) == ("Fighting",)


@cache
def _pool_and_filler(energy: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    pool = full_card_pool(energy)
    return pool, filler_order([d.decklist for d in load_meta(SNAPSHOT)], pool)


@pytest.mark.parametrize("axis", [PIKACHU_EX, MEGA_BLAZIKEN_EX])
def test_builds_a_legal_seed_around_the_axis(axis: str) -> None:
    constraints = DeckConstraints.of({axis: 2})
    energy = axis_energy(constraints.required, LUCARIO.energy)
    _, filler = _pool_and_filler(energy)
    seed = build_seed(LUCARIO, constraints, filler)
    assert seed is not None
    assert seed.size == 20
    assert seed.energy == energy
    assert seed.count_of(axis) == 2
    assert violations(seed) == ()
    assert constraints.violations(seed) == ()


def test_seed_without_axis_drops_excluded_cards() -> None:
    constraints = DeckConstraints.of(excluded=[POKE_BALL])
    _, filler = _pool_and_filler(LUCARIO.energy)
    seed = build_seed(LUCARIO, constraints, filler)
    assert seed is not None
    assert seed.count_of(POKE_BALL_PROMO) == 0
    assert seed.energy == LUCARIO.energy


def test_search_moves_never_break_the_constraints() -> None:
    constraints = DeckConstraints.of({PIKACHU_EX: 2}, excluded=[POKE_BALL])
    energy = axis_energy(constraints.required, LUCARIO.energy)
    pool, filler = _pool_and_filler(energy)
    seed = build_seed(LUCARIO, constraints, filler)
    assert seed is not None

    found = neighbours(seed, pool, similar_width=8, constraints=constraints)
    assert found
    assert all(constraints.violations(candidate) == () for candidate in found)

    rng = random.Random(3)
    other = build_seed(load_meta_recipe(3), constraints, filler)
    assert other is not None
    for _ in range(10):
        child = crossover(seed, other, rng=rng, constraints=constraints)
        assert constraints.violations(child) == ()
        mutated = mutate(child, pool, rng=rng, constraints=constraints)
        assert constraints.violations(mutated) == ()


def load_meta_recipe(index: int) -> DeckRecipe:
    return DeckRecipe.from_text(load_meta(SNAPSHOT)[index].decklist)
