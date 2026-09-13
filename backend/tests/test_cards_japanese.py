"""カード名・ワザ名の和名への置き換えのテスト。

対戦ログや相性表を日本語で追えるようにする。エンジンは英名のまま動かし、
表示するときだけ置き換える。
"""

import json
from pathlib import Path

import pytest

from pocket_api.cards.japanese import Translator

NAMES = {
    "schema_version": 1,
    "cards": {
        "B3b 001": {"english": "Caterpie", "name": "キャタピー", "attacks": {}, "abilities": {}},
        "B3b 003": {
            "english": "Butterfree",
            "name": "バタフリー",
            "attacks": {"Sunny Wind": "おひさまウインド"},
            "abilities": {"Sunny Cocoon": "ひなたのマユ"},
        },
        "A1 129": {
            "english": "Mewtwo ex",
            "name": "ミュウツーex",
            "attacks": {"Psydrive": "サイコドライブ"},
            "abilities": {},
        },
        "A1 151": {"english": "Mew", "name": "ミュウ", "attacks": {}, "abilities": {}},
        "A1 225": {"english": "Sabrina", "name": "ナツメ", "attacks": {}, "abilities": {}},
        "B3 008": {
            "english": "Mega Sceptile ex",
            "name": "メガジュカインex",
            "attacks": {},
            "abilities": {},
        },
    },
    "missing": [],
}


@pytest.fixture
def translator() -> Translator:
    return Translator.from_cards(NAMES["cards"])


def test_card_name_falls_back_to_english(translator: Translator) -> None:
    assert translator.card_name("Butterfree") == "バタフリー"
    assert translator.card_name("Unknownmon") == "Unknownmon"


def test_text_replaces_card_names_longest_first(translator: Translator) -> None:
    """`Mewtwo ex` の中の `Mew` を先に置き換えてはいけない。"""
    assert translator.text("Mewtwo ex をバトル場に出す") == "ミュウツーex をバトル場に出す"
    assert translator.text("Mew をベンチ1に出す") == "ミュウ をベンチ1に出す"


def test_text_replaces_attack_and_ability_names_in_brackets(translator: Translator) -> None:
    assert (
        translator.text("Butterfree がワザ「Sunny Wind」（60 ダメージ）")
        == "バタフリー がワザ「おひさまウインド」（60 ダメージ）"
    )
    assert translator.text("トレーナーズ「Sabrina」を使う") == "トレーナーズ「ナツメ」を使う"
    assert translator.text("特性「Sunny Cocoon」") == "特性「ひなたのマユ」"


def test_text_leaves_unknown_words(translator: Translator) -> None:
    assert translator.text("ワザ「Nothing」（0 ダメージ）") == "ワザ「Nothing」（0 ダメージ）"


def test_deck_name_translates_each_card(translator: Translator) -> None:
    assert translator.text("Butterfree Mega Sceptile ex") == "バタフリー メガジュカインex"


def test_from_file_reads_names_json(tmp_path: Path) -> None:
    path = tmp_path / "names_ja.json"
    path.write_text(json.dumps(NAMES, ensure_ascii=False), encoding="utf-8")
    assert Translator.from_file(path).card_name("Caterpie") == "キャタピー"


def test_missing_file_gives_identity_translator(tmp_path: Path) -> None:
    """和名ファイルが無くても落ちず、英名のまま出す。"""
    translator = Translator.from_file(tmp_path / "none.json")
    assert translator.text("Butterfree がワザ「Sunny Wind」") == "Butterfree がワザ「Sunny Wind」"
