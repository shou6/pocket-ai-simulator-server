"""アーキタイプ判定のテスト。limitless の分類を正解にする（requirements 8 章）。"""

import json
from pathlib import Path

import pytest

from pocket_api.optimize.archetype import (
    classify,
    key_pokemon,
    meta_archetypes,
    split_archetype_name,
)
from pocket_api.optimize.recipe import DeckRecipe

SNAPSHOT = json.loads(
    Path("/workspace/data/meta/2026-09-07_B4a-standard.json").read_text(encoding="utf-8")
)
ARCHETYPES = SNAPSHOT["archetypes"]
META = meta_archetypes([(a["name"], a["slug"], a["decklists"]) for a in ARCHETYPES])


def test_splits_an_archetype_name_into_pokemon_without_double_counting() -> None:
    names = ["Lucario", "Mega Lucario ex", "Riolu"]
    assert split_archetype_name("Mega Lucario ex Lucario", names) == {"Mega Lucario ex", "Lucario"}
    assert split_archetype_name("Mega Lucario ex", names) == {"Mega Lucario ex"}


def test_every_meta_archetype_gets_a_signature() -> None:
    assert len(META) == len(ARCHETYPES)
    lucario = next(m for m in META if m.slug.startswith("mega-lucario"))
    assert lucario.signature == {"Mega Lucario ex", "Lucario"}


@pytest.mark.parametrize(
    ("slug", "decklist"),
    [(a["slug"], text) for a in ARCHETYPES for text in a["decklists"]],
)
def test_classifies_every_limitless_decklist_into_its_own_archetype(
    slug: str, decklist: str
) -> None:
    result = classify(DeckRecipe.from_text(decklist), META)
    assert result.in_meta
    assert result.slug == slug


def test_prefers_the_more_specific_archetype() -> None:
    """名前に出てくるポケモンを多く含むアーキタイプを採る。"""
    lucario = next(a for a in ARCHETYPES if a["slug"].startswith("mega-lucario"))
    result = classify(DeckRecipe.from_text(lucario["decklists"][0]), META)
    assert result.name == "Mega Lucario ex Lucario"
    assert len(result.key_cards) == 2


def test_names_an_off_meta_deck_after_its_key_pokemon() -> None:
    """環境に無いデッキは、主要なポケモン（進化前は除き、ex を先に）で名前を作る。"""
    text = Path("/workspace/data/decks/elf-bazooka.txt").read_text(encoding="utf-8")
    result = classify(DeckRecipe.from_text(text), META)
    assert not result.in_meta
    assert result.slug is None
    assert result.name == "Whimsicott ex Ariados"


def test_key_pokemon_agree_with_limitless_names_most_of_the_time() -> None:
    """名前を作る規則の当たり具合。環境の 50 デッキで、名前のポケモンと一致する割合。

    2026-09-13 の実測で 42 / 50。外れはスイクン（パオジアンex を拾う）とマタドガス
    （メガヤミラミex を拾う）で、どちらも ex を 2 体以上積むデッキ。
    """
    agree = 0
    total = 0
    for archetype in META:
        decklists = next(a["decklists"] for a in ARCHETYPES if a["slug"] == archetype.slug)
        for text in decklists:
            total += 1
            keys = key_pokemon(DeckRecipe.from_text(text), len(archetype.signature))
            agree += {card.name for card in keys} == archetype.signature
    assert agree >= 40, f"{agree} / {total}"
