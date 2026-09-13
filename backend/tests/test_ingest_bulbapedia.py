"""Bulbapedia からカードの和名を取り込む処理のテスト。

ネットワークには触れず、API の応答フィクスチャで確認する。
対戦ログを日本語で追えるように、カード名・ワザ名・特性名の和名を集める。
"""

import json
from pathlib import Path

import pytest

from pocket_api.ingest.bulbapedia import (
    CardNames,
    build_names_file,
    fetch_card_names,
    page_title,
    parse_query_response,
    parse_wikitext,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def query_response() -> str:
    return (FIXTURES / "bulbapedia_query.json").read_text(encoding="utf-8")


def test_page_title_uses_expansion_name_and_number_without_padding() -> None:
    """deckgym の ID `B3b 003` から Bulbapedia のページ名を組み立てる。"""
    assert page_title("B3b 003", "Butterfree") == "Butterfree (Everyday Wonders 3)"
    assert page_title("P-A 007", "Professor's Research") == "Professor's Research (Promo-A 7)"
    assert page_title("A1 129", "Mewtwo ex") == "Mewtwo ex (Genetic Apex 129)"


def test_page_title_rejects_unknown_set() -> None:
    with pytest.raises(ValueError, match="Z9"):
        page_title("Z9 001", "Nobody")


def test_parse_wikitext_collects_names_attacks_and_abilities() -> None:
    text = (
        "{{TCG Card Infobox/Pokémon/Pocket\n|en name=Butterfree\n|ja name=バタフリー\n}}\n"
        "{{Cardtext/Ability/Pocket\n|name=Sunny Cocoon\n|jname=ひなたのマユ\n}}\n"
        "{{Cardtext/Attack/Pocket\n|name=Sunny Wind\n|jname=おひさまウインド\n|damage=60\n}}\n"
    )
    names = parse_wikitext(text)
    assert names == CardNames(
        name="バタフリー",
        attacks={"Sunny Wind": "おひさまウインド"},
        abilities={"Sunny Cocoon": "ひなたのマユ"},
    )


def test_parse_wikitext_flattens_icon_templates() -> None:
    """`ミュウツー{{TCGP Icon|ex}}` は `ミュウツーex` にする。"""
    text = "{{TCG Card Infobox/Pokémon/Pocket\n|ja name=ミュウツー{{TCGP Icon|ex}}\n}}\n"
    assert parse_wikitext(text).name == "ミュウツーex"
    text = "{{TCG Card Infobox/Pokémon/Pocket\n|ja name=メガアブソル{{TCGP Icon|Mega ex}}\n}}\n"
    assert parse_wikitext(text).name == "メガアブソルex"
    text = "{{TCG Card Infobox/Pokémon/Pocket\n|ja name=ツボツボ {{TCGP Icon|ex}}\n}}\n"
    assert parse_wikitext(text).name == "ツボツボex"


def test_parse_wikitext_without_japanese_name_returns_none() -> None:
    assert parse_wikitext("{{TCG Card Infobox/Trainer/Pocket\n|en name=Sabrina\n}}") is None


def test_parse_query_response_maps_requested_titles(query_response: str) -> None:
    """リダイレクト（別イラストの番号）も元のページ名に解決して返す。"""
    found, missing = parse_query_response(query_response)
    assert found["Butterfree (Everyday Wonders 3)"].name == "バタフリー"
    assert found["Sabrina (Genetic Apex 225)"].attacks == {}
    assert found["Mewtwo ex (Genetic Apex 262)"].name == "ミュウツーex"
    assert missing == ["Castform Sunny Form (Mega Rising 29)"]


class FakeClient:
    """API 応答のフィクスチャを返す。呼ばれたパスを記録する。"""

    def __init__(self, body: str) -> None:
        self.body = body
        self.paths: list[str] = []

    def get(self, path: str, *, use_cache: bool = True) -> str:
        self.paths.append(path)
        if "list=search" in path:
            return json.dumps(
                {"query": {"search": [{"title": "Castform Sunny Form (Pulsing Aura 24)"}]}}
            )
        if "Pulsing%20Aura%2024" in path or "Pulsing Aura 24" in path:
            return json.dumps(
                {
                    "query": {
                        "pages": {
                            "9": {
                                "title": "Castform Sunny Form (Pulsing Aura 24)",
                                "revisions": [
                                    {
                                        "slots": {
                                            "main": {
                                                "*": "{{TCG Card Infobox/Pokémon/Pocket\n"
                                                "|ja name=ポワルン たいようのすがた\n}}\n"
                                            }
                                        }
                                    }
                                ],
                            }
                        }
                    }
                }
            )
        return self.body


def test_fetch_card_names_batches_titles_and_falls_back_to_search(query_response: str) -> None:
    """50 件ずつまとめて取り、見つからないカードは検索で探し直す。"""
    client = FakeClient(query_response)
    cards = [
        ("B3b 003", "Butterfree"),
        ("A1 129", "Mewtwo ex"),
        ("A1 225", "Sabrina"),
        ("B1 029", "Castform Sunny Form"),
    ]
    result = fetch_card_names(client, cards)

    assert result["B3b 003"].name == "バタフリー"
    assert result["A1 129"].attacks["Psydrive"] == "サイコドライブ"
    assert result["A1 225"].name == "ナツメ"
    assert result["B1 029"].name == "ポワルン たいようのすがた"
    # 1 回目はまとめ取り、2 回目は検索、3 回目は検索結果のページ
    assert len(client.paths) == 3
    assert "titles=" in client.paths[0]
    assert "list=search" in client.paths[1]


def test_build_names_file_records_source_and_missing_cards() -> None:
    cards = [("B3b 003", "Butterfree"), ("Z1 001", "Ghost")]
    found = {"B3b 003": CardNames(name="バタフリー", attacks={}, abilities={})}
    data = build_names_file(cards, found)

    assert data["schema_version"] == 1
    assert "bulbapedia" in data["source"].lower()
    assert data["cards"]["B3b 003"]["name"] == "バタフリー"
    assert data["cards"]["B3b 003"]["english"] == "Butterfree"
    assert data["missing"] == ["Z1 001"]


def test_build_names_file_applies_manual_overrides() -> None:
    """Bulbapedia に無いカードは手動登録（name_overrides.json）で補う。"""
    cards = [("P-B 056", "Mega Heracross ex")]
    overrides = {
        "P-B 056": {"name": "メガヘラクロスex", "attacks": {"Dynamic Horn": "ダイナミックホーン"}}
    }
    data = build_names_file(cards, {}, overrides)

    assert data["cards"]["P-B 056"]["english"] == "Mega Heracross ex"
    assert data["cards"]["P-B 056"]["name"] == "メガヘラクロスex"
    assert data["cards"]["P-B 056"]["attacks"] == {"Dynamic Horn": "ダイナミックホーン"}
    assert data["cards"]["P-B 056"]["abilities"] == {}
    assert data["missing"] == []
