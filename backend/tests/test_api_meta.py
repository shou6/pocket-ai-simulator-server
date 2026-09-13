"""メタデッキ一覧 API のテスト（F-01）。

受け入れ条件は「limitless のスナップショットが日付付きで表示される」。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pocket_api.ingest.store import import_snapshot

SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")


@pytest.fixture
def seeded(db_session: Session, api: TestClient) -> TestClient:
    import_snapshot(db_session, SNAPSHOT)
    db_session.flush()
    return api


def test_lists_the_latest_snapshot_with_its_date(seeded: TestClient) -> None:
    response = seeded.get("/meta/decks")
    assert response.status_code == 200
    body = response.json()
    assert body["fetched_at"].startswith("2026-09-07")
    assert body["format"] == "standard"
    assert body["set"] == "B4a"
    assert len(body["decks"]) == 10


def test_decks_are_ordered_by_rank_with_share_and_win_rate(seeded: TestClient) -> None:
    body = seeded.get("/meta/decks").json()
    ranks = [d["rank"] for d in body["decks"]]
    assert ranks == sorted(ranks)
    top = body["decks"][0]
    assert top["name"] == "Mega Lucario ex Lucario"
    assert 0 < top["share"] < 1
    assert 0 < top["win_rate"] < 1
    assert top["count"] > 0


def test_returns_404_when_nothing_has_been_ingested(api: TestClient) -> None:
    response = api.get("/meta/decks")
    assert response.status_code == 404


def test_each_deck_carries_its_representative_list_with_japanese_names(seeded: TestClient) -> None:
    top = seeded.get("/meta/decks").json()["decks"][0]
    assert top["name_ja"] != top["name"], "アーキタイプ名も和名にする"
    deck = top["decklist"]
    assert deck["size"] == 20
    assert deck["energy"] == ["Fighting"]
    names = {row["card"]["name_ja"] for row in deck["cards"]}
    assert "リオル" in names
    assert "Energy: Fighting" in deck["decklist"]


def test_does_not_expose_the_source_url(seeded: TestClient) -> None:
    """取得元の URL は画面に出さないので、応答にも含めない（DB には残す）。"""
    body = seeded.get("/meta/decks").json()
    assert "source_url" not in body
