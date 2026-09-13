"""デッキ QR の中身（デッキコード）の読み書きのテスト（F-11 / F-12）。

実機が発行した QR を復号した文字列（`tests/fixtures/deck_codes.json`）で確かめる。
フォーマットは `docs/deck-qr.md` 2 節。
"""

import base64
import json
import re
from pathlib import Path

import pocket_engine_py as engine
import pytest

from pocket_api.cards.deck_builder_ids import (
    DEFAULT_OUT,
    DeckBuilderIds,
    build_ids_file,
)
from pocket_api.cards.deck_code import decode_deck_code, encode_deck_code
from pocket_api.optimize.recipe import DeckRecipe

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "deck_codes.json").read_text(encoding="utf-8")
)
FLIBUSTIER = Path("/workspace/data/raw/flibustier/dist/cards.json")
SAMPLE003 = Path("/workspace/data/decks/mydeck_sample003.txt")
SAMPLE003_CODE = (
    "DJiWnpiWnpiZRpicypicypiWqJiWqJiXKpiXwJiYkpia1picrAgAGEIAGEIANn4ANn4AVLoAVLoAVMQAVMQBAw=="
)


# --- 対応表 ---------------------------------------------------------------


@pytest.mark.skipif(not FLIBUSTIER.exists(), reason="data/raw は git 管理外")
def test_every_print_matches_the_asset_name_pattern() -> None:
    cards = json.loads(FLIBUSTIER.read_text(encoding="utf-8"))
    assert len(cards) == 3879
    for card in cards:
        match = re.match(r"c(PK|TR)_\d+_(\d{6})_", card["image"])
        assert match is not None, card
        assert int(match.group(2)) % 10 == 0, card


@pytest.mark.skipif(not FLIBUSTIER.exists(), reason="data/raw は git 管理外")
def test_generated_ids_match_the_engine_exactly() -> None:
    data = build_ids_file(json.loads(FLIBUSTIER.read_text(encoding="utf-8")))
    assert len(data["cards"]) == 2318
    prints = {card_id for ids in data["cards"].values() for card_id in ids}
    engine_ids = {status.id for status in engine.card_statuses_all()}
    # PROMO- を P- に揃えると完全に一致する（docs/deck-qr.md 3 節）
    assert prints == engine_ids


def test_committed_ids_file_is_loadable() -> None:
    ids = DeckBuilderIds.from_file(DEFAULT_OUT)
    assert ids.key_of("A1 001") == ("PK", 1)
    assert ids.key_of("P-A 005") == ids.key_of("A2b 111") == ("TR", 3)
    # 再録が複数あるときはセット名と番号の昇順で先頭を代表にする
    assert ids.representative("TR", 3) == "A2b 111"


# --- デコード -------------------------------------------------------------


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["name"] for f in FIXTURES])
def test_decodes_real_codes_into_valid_decks(fixture: dict[str, object]) -> None:
    recipe = decode_deck_code(str(fixture["code"]))
    assert recipe.size == 20
    engine.validate_deck(recipe.to_text())
    assert recipe == DeckRecipe.from_text(str(fixture["decklist"]))


def test_decodes_the_sample_deck_screen() -> None:
    recipe = decode_deck_code(SAMPLE003_CODE)
    assert recipe == DeckRecipe.from_text(SAMPLE003.read_text(encoding="utf-8"))
    assert recipe.energy == ("Water",)


def _raw(sections: list[list[int]], energies: list[int]) -> str:
    out = bytearray()
    for values in sections:
        out.append(len(values))
        for value in values:
            out += value.to_bytes(3, "big")
    out.append(len(energies))
    out += bytes(energies)
    return base64.b64encode(bytes(out)).decode()


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("これは Base64 ではない", "Base64"),
        ("", "短すぎ"),
        (base64.b64encode(bytes([3, 0, 0])).decode(), "足りません"),
        (_raw([[10_000_030], [6210]], [3]), "20 枚"),
        (_raw([[10_000_030] * 10, [6210] * 10], [9]), "エネルギー"),
        (_raw([[10_000_030] * 10, [6210] * 10], []), "エネルギー"),
        (_raw([[6210] * 10, [6210] * 10], [3]), "トレーナーズ"),
    ],
)
def test_rejects_broken_codes(code: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decode_deck_code(code)


def test_rejects_trailing_bytes() -> None:
    """余りのバイトは黙って捨てない。"""
    raw = base64.b64decode(SAMPLE003_CODE) + b"\x00"
    with pytest.raises(ValueError, match="余り"):
        decode_deck_code(base64.b64encode(raw).decode())


def test_rejects_unknown_card_numbers() -> None:
    code = _raw([[10_000_030] * 10, [99_999 * 10] * 10], [3])
    with pytest.raises(ValueError, match="見つかりません"):
        decode_deck_code(code)


# --- エンコード -----------------------------------------------------------


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["name"] for f in FIXTURES])
def test_round_trips_real_codes(fixture: dict[str, object]) -> None:
    """decode → encode → decode で中身が変わらない。

    文字列そのものは一致しない。実機はポケモン区画をデッキ画面の並び
    （進化ラインごと、並びは任意）で書くので、中身だけからは復元できない。
    トレーナーズ区画は（グッズ → どうぐ → サポート → スタジアム、番号順）で一致する。
    """
    original = str(fixture["code"])
    recipe = decode_deck_code(original)
    encoded = encode_deck_code(recipe)
    assert decode_deck_code(encoded) == recipe
    trainers = base64.b64decode(original)[: 1 + 3 * base64.b64decode(original)[0]]
    assert base64.b64decode(encoded)[: len(trainers)] == trainers


def test_encoding_ignores_which_print_is_used() -> None:
    """再録違いを選んでも同じコードになる。見るのは deckBuilderNr だけ。"""
    a = SAMPLE003.read_text(encoding="utf-8")
    b = a.replace("2 A2b 111", "2 P-A 005")
    assert encode_deck_code(DeckRecipe.from_text(a)) == encode_deck_code(DeckRecipe.from_text(b))


def test_encoding_mixed_prints_of_one_card() -> None:
    text = SAMPLE003.read_text(encoding="utf-8").replace("2 A2b 111", "1 A2b 111\n1 P-A 005")
    recipe = decode_deck_code(encode_deck_code(DeckRecipe.from_text(text)))
    assert recipe.count_of("A2b 111") == 2


def test_encoding_rejects_invalid_decks() -> None:
    small = DeckRecipe(cards=(("A1 001", 2),), energy=("Grass",))
    with pytest.raises(ValueError, match="20 枚"):
        encode_deck_code(small)
    base = DeckRecipe.from_text(SAMPLE003.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="エネルギー"):
        encode_deck_code(DeckRecipe(cards=base.cards, energy=()))
    with pytest.raises(ValueError, match="エネルギー"):
        encode_deck_code(DeckRecipe(cards=base.cards, energy=("Grass", "Fire", "Water", "Metal")))
    with pytest.raises(ValueError, match="エネルギー"):
        encode_deck_code(DeckRecipe(cards=base.cards, energy=("Dragon",)))
