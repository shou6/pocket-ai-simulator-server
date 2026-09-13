"""持っていないカードの代わり（F-10）のテスト。"""

import pytest

from pocket_api.optimize.alternatives import alternatives
from pocket_api.optimize.recipe import DeckRecipe, violations

# ユーザーが見た改善後の悪デッキ（ダークライex・パルデアドオーex・ロケット団のマタドガスex）
DARK_POISON = DeckRecipe.from_text(
    "Energy: Darkness\n"
    "1 A2 110\n2 A2b 047\n1 A3a 042\n2 B4a 042\n2 A2b 048\n2 B4a 043\n"
    "2 A2b 111\n2 A3 146\n1 B1 219\n1 A1a 068\n2 A4b 373\n2 B1 225\n"
)
HEAVY_HELMET = "B1 219"


def test_the_sample_deck_is_legal() -> None:
    assert DARK_POISON.size == 20
    assert violations(DARK_POISON) == ()


def test_offers_only_cards_that_work_in_the_deck() -> None:
    """ヘビーメットの代わりに、鋼・水・2 進化・古代向けのどうぐを出さない。"""
    found = alternatives(DARK_POISON, HEAVY_HELMET, limit=6)
    assert found
    for bad in ("B2 148", "A4a 067", "A4 153", "B3a 070", "B2a 087"):
        assert bad not in found
    for card_id in found:
        swapped = DARK_POISON.with_change(HEAVY_HELMET, -1).with_change(card_id, 1)
        assert violations(swapped) == ()


def test_skips_excluded_cards_and_their_reprints() -> None:
    first = alternatives(DARK_POISON, HEAVY_HELMET, limit=3)
    again = alternatives(DARK_POISON, HEAVY_HELMET, limit=3, excluded=[first[0]])
    assert first[0] not in again


def test_rejects_a_card_that_is_not_in_the_deck() -> None:
    with pytest.raises(ValueError, match="入っていない"):
        alternatives(DARK_POISON, "A1 001")
