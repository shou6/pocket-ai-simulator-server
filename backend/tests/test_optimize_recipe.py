"""デッキの表現と制約のテスト。

探索ではデッキをカードの多重集合として扱い、テキストとの相互変換で
エンジンに渡す。制約は 20 枚・同名 2 枚以内・たね 1 枚以上・所持カード。
"""

import pytest

from pocket_api.optimize.recipe import DeckRecipe, Ownership, violations

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


def test_roundtrip_between_text_and_recipe() -> None:
    recipe = DeckRecipe.from_text(FIRE_TEXT)
    assert recipe.size == 20
    assert recipe.energy == ("Fire",)
    # テキストに戻してエンジンが受け付けること
    again = DeckRecipe.from_text(recipe.to_text())
    assert again == recipe


def test_valid_deck_has_no_violations() -> None:
    assert violations(DeckRecipe.from_text(FIRE_TEXT)) == ()


def test_detects_wrong_card_count() -> None:
    recipe = DeckRecipe.from_text(FIRE_TEXT).with_change("A1 042", -1)
    assert recipe.size == 19
    assert any("20 枚" in v for v in violations(recipe))


def test_detects_more_than_two_of_a_name() -> None:
    # 同名は 2 枚まで。同じ名前の別セットも合算する
    recipe = DeckRecipe.from_text(FIRE_TEXT).with_change("A1 042", 1).with_change("A1 043", -1)
    assert any("2 枚" in v for v in violations(recipe))


def test_detects_no_basic_pokemon() -> None:
    # たねポケモンが 1 枚もないデッキは始められない
    only_trainers = DeckRecipe(cards=(("P-A 005", 20),), energy=("Fire",))
    assert any("たね" in v for v in violations(only_trainers))


def test_detects_cards_beyond_ownership() -> None:
    owned = Ownership({"A1 042": 1})
    recipe = DeckRecipe.from_text(FIRE_TEXT)
    found = violations(recipe, ownership=owned)
    assert any("所持" in v for v in found)


def test_ownership_allows_owned_cards() -> None:
    # 十分に持っていれば所持の違反は出ない
    owned = Ownership({cid: 2 for cid, _ in DeckRecipe.from_text(FIRE_TEXT).cards})
    assert violations(DeckRecipe.from_text(FIRE_TEXT), ownership=owned) == ()


def test_with_change_rejects_negative_count() -> None:
    recipe = DeckRecipe.from_text(FIRE_TEXT)
    with pytest.raises(ValueError, match="枚数"):
        recipe.with_change("A1 042", -5)


def test_rejects_a_card_that_cannot_work_in_the_deck() -> None:
    """そのデッキで働かないカードは制約違反。

    ユーザーの指摘：交叉でメガルカリオ（闘）とメガチルタリス（超）が混ざり、
    コルニ（闘ポケモン限定のダメージバフ）だけが取り残されていた。
    プールの制限は「新しく追加する候補」にしか効かず、既に入っているカードは
    除去されない。
    """
    # 超デッキにコルニ（B3 149、闘限定）を入れる
    text = """\
Energy: Psychic
2 B1 102
2 B1 184
2 B1 196
2 B1 225
2 B2 153
2 B2b 040
2 B3a 020
2 P-A 007
1 B3 149
1 A4a 059
2 A2b 111
"""
    recipe = DeckRecipe.from_text(text)
    problems = violations(recipe)
    assert any("コルニ" in p or "Korrina" in p for p in problems), problems


def test_accepts_a_conditional_card_in_a_matching_deck() -> None:
    """条件を満たすデッキなら通す（メガルカリオはコルニを正規採用している）。"""
    import json
    from pathlib import Path

    snap = json.loads(
        Path("/workspace/data/meta/2026-09-07_B4a-standard.json").read_text(encoding="utf-8")
    )
    for archetype in snap["archetypes"]:
        for decklist in archetype["decklists"]:
            recipe = DeckRecipe.from_text(decklist)
            assert violations(recipe) == (), f"{archetype['name']}: {violations(recipe)}"


def test_colorless_condition_is_met_by_a_colorless_pokemon() -> None:
    """イリマ（自分の [C] ポケモン）は無色のポケモンがいれば働く。

    以前は「無タイプのエネルギーのデッキでしか働かない」と判定し、実機のデッキ
    （mydeck_sample003、水エネルギーに無色のラッタex）を制約違反にしていた。
    """
    from pathlib import Path

    text = Path("/workspace/data/decks/mydeck_sample003.txt").read_text(encoding="utf-8")
    assert violations(DeckRecipe.from_text(text)) == ()


def test_tools_that_need_another_type_are_dead_in_the_deck() -> None:
    """どうぐの「この札を付けた [M] ポケモン」も条件として読む（悪デッキのメタルコアバリア）。"""
    lucario = DeckRecipe.from_text(
        "Energy: Fighting\n2 A2 091\n2 B3 081\n1 A2 092\n1 A1 155\n1 A1 154\n"
        "2 P-A 007\n2 B1 225\n1 A2b 070\n1 A2 150\n1 B3 149\n"
        "2 P-A 005\n1 P-A 002\n1 B2 145\n1 A2 147\n1 B3 154\n"
    )
    assert violations(lucario) == ()
    swapped = lucario.with_change("A2 147", -1).with_change(
        "B2 148", 1
    )  # 大きなマント → メタルコアバリア
    assert any("メタルコアバリア" in v or "Metal Core Barrier" in v for v in violations(swapped))
