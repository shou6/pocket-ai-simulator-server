"""表示用のカード情報（`cards.catalog`）のテスト。"""

from pocket_api.cards.catalog import card_view, deck_view
from pocket_api.optimize.recipe import DeckRecipe


def test_card_view_shows_the_lowest_rarity_print_of_a_reprint() -> None:
    view = card_view("A4b 373")
    assert view is not None
    assert view.id == "P-A 007"
    assert view.name_ja == "博士の研究"


def test_deck_view_merges_reprints() -> None:
    recipe = DeckRecipe(cards=(("A2b 111", 1), ("P-A 005", 1), ("A1 001", 2)), energy=("Grass",))
    view = deck_view(recipe)
    assert [(row.card.id, row.count) for row in view.cards] == [("A1 001", 2), ("P-A 005", 2)]
    assert "A2b 111" not in view.decklist
