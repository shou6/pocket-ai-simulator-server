"""デッキの検証・デッキコード・診断 API のテスト（F-02 / F-07 / F-08 / F-11 / F-12）。"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pocket_api.ingest.store import import_snapshot

SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")
SAMPLE = Path("/workspace/data/decks/mydeck_sample003.txt").read_text(encoding="utf-8")
CODES = json.loads(
    (Path(__file__).parent / "fixtures" / "deck_codes.json").read_text(encoding="utf-8")
)


def test_validate_accepts_a_proper_deck_and_names_its_cards(api: TestClient) -> None:
    body = api.post("/decks/validate", json={"decklist": SAMPLE}).json()
    # スナップショットが無くても、主要なポケモンから名前を作って返す
    assert body["archetype"]["in_meta"] is False
    assert body["archetype"]["name_ja"]
    assert body["ok"] is True
    assert body["problems"] == []
    assert body["deck"]["size"] == 20
    names = [row["card"]["name_ja"] for row in body["deck"]["cards"]]
    assert "アローラロコン" in names or any("ロコン" in n for n in names)
    # ポケモンから先に並べる
    assert body["deck"]["cards"][0]["card"]["kind"] == "ポケモン"


def test_validate_explains_what_is_wrong(api: TestClient) -> None:
    short = "\n".join(SAMPLE.splitlines()[:-1])
    body = api.post("/decks/validate", json={"decklist": short}).json()
    assert body["ok"] is False
    assert any("20 枚" in p for p in body["problems"])


def test_validate_reports_unreadable_lines(api: TestClient) -> None:
    body = api.post("/decks/validate", json={"decklist": "Energy: Water\nこんにちは"}).json()
    assert body["ok"] is False
    assert body["deck"] is None
    assert "読み取れない" in body["problems"][0]


def test_validate_names_unknown_cards(api: TestClient) -> None:
    text = SAMPLE.replace("2 A3 040", "2 Z9 999")
    body = api.post("/decks/validate", json={"decklist": text}).json()
    assert body["ok"] is False
    assert [c["id"] for c in body["unimplemented"]] == ["Z9 999"]


def test_decode_returns_the_deck(api: TestClient) -> None:
    response = api.post("/decks/decode", json={"code": CODES[0]["code"]})
    assert response.status_code == 200
    body = response.json()
    assert body["deck"]["size"] == 20
    assert body["validation"]["ok"] is True


def test_decode_rejects_broken_codes(api: TestClient) -> None:
    response = api.post("/decks/decode", json={"code": "!!!"})
    assert response.status_code == 422
    assert "Base64" in response.json()["detail"]


def test_encode_round_trips(api: TestClient) -> None:
    code = api.post("/decks/encode", json={"decklist": SAMPLE}).json()["code"]
    deck = api.post("/decks/decode", json={"code": code}).json()["deck"]
    again = api.post("/decks/validate", json={"decklist": deck["decklist"]}).json()["deck"]
    original = api.post("/decks/validate", json={"decklist": SAMPLE}).json()["deck"]
    assert again == original


def test_encode_rejects_an_invalid_deck(api: TestClient) -> None:
    response = api.post("/decks/encode", json={"decklist": "Energy: Water\n2 A1 001"})
    assert response.status_code == 422


@pytest.fixture
def seeded(db_session: Session, api: TestClient) -> TestClient:
    import_snapshot(db_session, SNAPSHOT)
    db_session.flush()
    return api


def test_diagnose_returns_rates_with_their_precision(seeded: TestClient) -> None:
    response = seeded.post("/decks/diagnose", json={"decklist": SAMPLE, "games": 60})
    assert response.status_code == 200
    body = response.json()
    low, high = body["interval"]
    assert low <= body["overall"] <= high
    assert len(body["matchups"]) == 10
    assert body["precision"] == {
        "strategy": "l",
        "games": 60,
        "noise": pytest.approx(0.01826, abs=1e-4),
    }
    assert body["snapshot_fetched_at"].startswith("2026-09-07")
    assert body["matchups"][0]["name_ja"]


def test_diagnose_only_accepts_the_three_game_options(seeded: TestClient) -> None:
    response = seeded.post("/decks/diagnose", json={"decklist": SAMPLE, "games": 100})
    assert response.status_code == 422


def test_diagnose_refuses_a_deck_it_cannot_evaluate(seeded: TestClient) -> None:
    text = SAMPLE.replace("2 A3 040", "2 Z9 999")
    response = seeded.post("/decks/diagnose", json={"decklist": text, "games": 60})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["validation"]["unimplemented"][0]["id"] == "Z9 999"


def test_swap_measures_the_replacement_against_the_original(seeded: TestClient) -> None:
    body = {"decklist": SAMPLE, "out_card": "A2b 111", "in_card": "A1 225", "games": 60}
    response = seeded.post("/decks/swap", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["delta"] == pytest.approx(result["win_rate"] - result["base_win_rate"])
    assert result["verdict"] in {"clear", "likely", "none", "worse"}
    assert result["deck"]["size"] == 20


def test_swap_refuses_a_deck_that_breaks_the_rules(seeded: TestClient) -> None:
    # A2 150（サカキ）はもう 1 枚入っている。3 枚目にはできない
    body = {"decklist": SAMPLE, "out_card": "A2b 111", "in_card": "A2b 070", "games": 60}
    text = SAMPLE
    assert "1 A2 150" in text
    body["in_card"] = "A2 150"
    body["decklist"] = text.replace("1 A2 150", "2 A2 150").replace("1 A3 149\n", "")
    response = seeded.post("/decks/swap", json=body)
    assert response.status_code == 422


def test_validate_tells_which_meta_archetype_the_deck_is(seeded: TestClient) -> None:
    body = seeded.post("/decks/validate", json={"decklist": SAMPLE}).json()
    archetype = body["archetype"]
    assert archetype["in_meta"] is True
    assert archetype["slug"].startswith("team-rockets-raticate-ex")
    assert archetype["name_ja"] == "ロケット団のラッタex アローラキュウコンex"
    assert {c["name"] for c in archetype["key_cards"]} == {
        "Team Rocket's Raticate ex",
        "Alolan Ninetales ex",
    }


def test_alternatives_only_offer_cards_that_work_in_the_deck(api: TestClient) -> None:
    """持っていないカードの代わりは、そのデッキに入れて働くものだけ（F-10）。"""
    dark = (
        "Energy: Darkness\n"
        "1 A2 110\n2 A2b 047\n1 A3a 042\n2 B4a 042\n2 A2b 048\n2 B4a 043\n"
        "2 A2b 111\n2 A3 146\n1 B1 219\n1 A1a 068\n2 A4b 373\n2 B1 225\n"
    )
    body = {"decklist": dark, "card_id": "B1 219", "limit": 6, "excluded": ["A2 148"]}
    response = api.post("/decks/alternatives", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["card"]["name_ja"] == "ヘビーメット"
    ids = [card["id"] for card in result["alternatives"]]
    assert 0 < len(ids) <= 6
    assert "B2 148" not in ids, "メタルコアバリア（鋼）は悪デッキで働かない"
    assert "A2 148" not in ids, "使わないカードは出さない"


def test_alternatives_reject_a_card_outside_the_deck(api: TestClient) -> None:
    response = api.post("/decks/alternatives", json={"decklist": SAMPLE, "card_id": "B1 219"})
    assert response.status_code == 422
