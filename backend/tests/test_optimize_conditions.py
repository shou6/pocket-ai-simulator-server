# ruff: noqa: E501  効果文はカードの原文をそのまま載せる
"""カードの効果の条件（そのデッキで働くか）の読み取りのテスト。

どうぐに多い「この札を付けた [X] ポケモン」や、進化段階・古代／未来の条件を読めず、
悪デッキの代わりのカードにメタルコアバリア（鋼）やウォーターボート（水）が並んでいた
（2026-09-13 のユーザーの指摘）。
"""

import pytest

from pocket_api.optimize.conditions import DeckPokemon, Requirement, requirement_of, unmet


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        # 自分の [X] ポケモン（コルニ。以前から読めていた形）
        (
            "During this turn, attacks used by your [F] Pokémon do +30 damage to your opponent's Active Pokémon ex.",
            Requirement(types=frozenset({"闘"})),
        ),
        # バトル場・ベンチの [X] ポケモン
        (
            "Attach a [R] Energy from your discard pile to your Active [R] Pokémon.",
            Requirement(types=frozenset({"炎"})),
        ),
        (
            "Flip a coin. If heads, take a [L] Energy from your Energy Zone and attach it to 1 of your Benched [L] Pokémon.",
            Requirement(types=frozenset({"雷"})),
        ),
        # どうぐ：この札を付けた [X] ポケモン
        # 前の文に opponent's があっても、条件は自分の側（メタルコアバリア）
        (
            "If this card is attached to 1 of your Pokémon, discard it at the end of your opponent's turn.The [M] Pokémon this card is attached to takes -50 damage from attacks from your opponent's Pokémon.",
            Requirement(types=frozenset({"鋼"})),
        ),
        (
            "The [M] Pokémon this card is attached to takes -10 damage from attacks from your opponent's Pokémon.",
            Requirement(types=frozenset({"鋼"})),
        ),
        (
            "If the [D] Pokémon this card is attached to is in the Active Spot and is damaged by an attack from your opponent's Pokémon, your opponent reveals a random card from their hand.",
            Requirement(types=frozenset({"悪"})),
        ),
        # 山札・トラッシュから [X] ポケモンを探す
        (
            "Put a random Basic [W] Pokémon from your discard pile into your hand.",
            Requirement(types=frozenset({"水"})),
        ),
        (
            "Look at the top card of your deck. If that card is a [P] Pokémon, put it into your hand.",
            Requirement(types=frozenset({"超"})),
        ),
        # 両者に効くスタジアムでも、[X] ポケモンがいなければ自分には働かない
        (
            "The Retreat Cost of each [P] Pokémon in play (both yours and your opponent's) is 2 less.",
            Requirement(types=frozenset({"超"})),
        ),
        # [X] エネルギーが付いていること
        (
            "Heal 40 damage from each of your Pokémon that has any [W] Energy attached.",
            Requirement(energies=frozenset({"水"})),
        ),
        # 進化段階
        (
            "The Stage 2 Pokémon this card is attached to has no Retreat Cost.",
            Requirement(stages=frozenset({2})),
        ),
        (
            "The Stage 1 Pokémon this card is attached to gets +30 HP.",
            Requirement(stages=frozenset({1})),
        ),
        # 古代・未来
        (
            "The Ancient Pokémon this card is attached to gets +40 HP.",
            Requirement(lineages=frozenset({"古代"})),
        ),
        (
            "Shuffle 1 of your Future Pokémon in play into your deck.",
            Requirement(lineages=frozenset({"未来"})),
        ),
        # メガシンカ ex を探す
        (
            "Put a random Mega Evolution Pokémon ex from your deck into your hand.",
            Requirement(mega_ex=True),
        ),
    ],
)
def test_reads_the_condition_of_an_effect(effect: str, expected: Requirement) -> None:
    assert requirement_of(effect) == expected


@pytest.mark.parametrize(
    "effect",
    [
        "Draw 2 cards.",
        # 化石は自分が無色のポケモンになるだけで、条件ではない
        "Play this card as if it were a 40-HP Basic [C] Pokémon.At any time during your turn, you may discard this card from play.",
        # 相手のポケモンを指す記述
        "Discard a [R] Energy from your opponent's Active Pokémon.",
        "During this turn, attacks used by your Pokémon do +20 damage to your opponent's Active Pokémon ex.",
        # 「メガシンカ ex 以外」は条件ではない
        "When applying the opponent's Active Pokémon's Weakness to damage from attacks used by Pokémon in play (both yours and your opponent's) that aren't Mega Evolution Pokémon ex, apply Weakness as ×2.",
    ],
)
def test_ignores_effects_without_a_condition_on_our_side(effect: str) -> None:
    assert requirement_of(effect).empty


def _pokemon(
    name: str, energy_type: str, stage: int = 0, lineage: str | None = None
) -> DeckPokemon:
    return DeckPokemon(name=name, energy_type=energy_type, stage=stage, lineage=lineage)


DARK_POISON = [
    _pokemon("Darkrai ex", "悪"),
    _pokemon("Paldean Wooper", "悪"),
    _pokemon("Paldean Clodsire ex", "悪", 1),
    _pokemon("Team Rocket's Koffing", "悪"),
    _pokemon("Team Rocket's Weezing ex", "悪", 1),
]


def test_tells_why_a_card_does_not_work_in_the_deck() -> None:
    metal = requirement_of("The [M] Pokémon this card is attached to takes -50 damage.")
    assert unmet(metal, DARK_POISON, {"悪"}) == "鋼ポケモンがいないと働きません"
    stage2 = requirement_of("The Stage 2 Pokémon this card is attached to has no Retreat Cost.")
    assert unmet(stage2, DARK_POISON, {"悪"}) == "2 進化のポケモンがいないと働きません"
    ancient = requirement_of("The Ancient Pokémon this card is attached to gets +40 HP.")
    assert unmet(ancient, DARK_POISON, {"悪"}) == "古代のポケモンがいないと働きません"
    water = requirement_of(
        "Heal 40 damage from each of your Pokémon that has any [W] Energy attached."
    )
    assert unmet(water, DARK_POISON, {"悪"}) == "水エネルギーのデッキでしか働きません"


def test_accepts_cards_whose_condition_the_deck_meets() -> None:
    dark = requirement_of(
        "If the [D] Pokémon this card is attached to is in the Active Spot, do 10 damage."
    )
    assert unmet(dark, DARK_POISON, {"悪"}) is None
    stage1 = requirement_of("The Stage 1 Pokémon this card is attached to gets +30 HP.")
    assert unmet(stage1, DARK_POISON, {"悪"}) is None
    # イリマ（自分の [C] ポケモン）は、無色のポケモンがいれば働く。エネルギーの種類は問わない
    colorless = requirement_of("Put 1 of your [C] Pokémon that has damage on it into your hand.")
    assert unmet(colorless, [_pokemon("Team Rocket's Raticate ex", "無", 1)], {"水"}) is None
