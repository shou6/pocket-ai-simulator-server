"""カード検索と役割の近いカードの API のテスト（F-09 / F-10）。"""

from fastapi.testclient import TestClient


def test_search_finds_cards_by_japanese_name_once_per_card(client: TestClient) -> None:
    body = client.get("/cards", params={"q": "モンスターボール"}).json()
    ids = [c["id"] for c in body["cards"]]
    # 再録はまとめ、レアリティの低い印刷（クラウンの A2b 111 ではなくプロモの ◆1）で出す
    assert "P-A 005" in ids
    assert "A2b 111" not in ids, "再録はまとめる"


def test_search_finds_cards_by_english_name(client: TestClient) -> None:
    body = client.get("/cards", params={"q": "pikachu"}).json()
    assert body["cards"]
    assert all("pikachu" in c["name"].lower() for c in body["cards"])


def test_similar_lists_cards_with_a_close_role(client: TestClient) -> None:
    body = client.get("/cards/A2b 111/similar", params={"limit": 3}).json()
    # 表示はレアリティの低い印刷にそろえる
    assert body["card"]["id"] == "P-A 005"
    assert 0 < len(body["similar"]) <= 3


def test_similar_404_for_unknown_cards(client: TestClient) -> None:
    assert client.get("/cards/Z9 999/similar").status_code == 404
