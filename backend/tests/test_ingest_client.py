"""limitless の取得クライアントのテスト。

実ネットワークには触れず、差し替え可能な取得関数で挙動を確認する。
確認したいのは、レート制限を守ること・同じページを取り直さないこと。
"""

from pathlib import Path

import pytest

from pocket_api.ingest.client import LimitlessClient


@pytest.fixture
def client(tmp_path: Path) -> LimitlessClient:
    return LimitlessClient(cache_dir=tmp_path, min_interval=0.0)


def test_get_returns_fetched_body(client: LimitlessClient) -> None:
    """取得した本文をそのまま返す。"""
    client._fetch = lambda url: f"<html>{url}</html>"  # type: ignore[method-assign]
    assert "decks" in client.get("/decks?game=POCKET")


def test_get_caches_pages_on_disk(client: LimitlessClient, tmp_path: Path) -> None:
    """同じページを 2 度取りに行かない。2 回目はキャッシュから返す。"""
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return "<html>1回目</html>"

    client._fetch = fetch  # type: ignore[method-assign]
    first = client.get("/decks?game=POCKET")
    second = client.get("/decks?game=POCKET")

    assert calls == ["https://play.limitlesstcg.com/decks?game=POCKET"]
    assert first == second
    assert list(tmp_path.glob("*.html"))


def test_get_refetches_when_cache_disabled(client: LimitlessClient) -> None:
    """キャッシュを使わない指定なら取り直す。"""
    calls: list[str] = []
    client._fetch = lambda url: (calls.append(url), "<html/>")[1]  # type: ignore[method-assign]

    client.get("/decks", use_cache=False)
    client.get("/decks", use_cache=False)

    assert len(calls) == 2


def test_get_waits_between_requests(tmp_path: Path) -> None:
    """連続取得の間隔を空ける。取得元に負荷をかけないため。"""
    slept: list[float] = []
    client = LimitlessClient(cache_dir=tmp_path, min_interval=1.0, sleep=slept.append)
    client._fetch = lambda url: "<html/>"  # type: ignore[method-assign]

    client.get("/a")
    client.get("/b")

    assert slept, "2 回目の取得前に待機していない"
    assert slept[0] > 0.0


def test_user_agent_does_not_leak_our_information(client: LimitlessClient) -> None:
    """User-Agent からこちらの情報が漏れないこと。

    非公開リポジトリの URL やプロジェクト名を外部サイトに送らない
    （2026-09-10 のユーザー指摘）。取得元への配慮はアクセス間隔で行う。
    """
    assert "github" not in client.user_agent.lower(), "リポジトリの URL を送らない"
    assert "pocketAiSimulator" not in client.user_agent, "プロジェクト名を送らない"
    assert "@" not in client.user_agent, "連絡先を送らない"
    assert client.user_agent.startswith("Mozilla/"), "一般的なブラウザと同じ名乗り"
