"""メタデッキのスナップショット作成のテスト。

ネットワークには触れず、フィクスチャを返すクライアントで確認する。
"""

import json
from pathlib import Path

import pytest

from pocket_api.ingest.snapshot import build_snapshot, snapshot_filename

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    """フィクスチャを返すクライアント。取得したパスを記録する。"""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def get(self, path: str, *, use_cache: bool = True) -> str:
        self.paths.append(path)
        if path.startswith("/decks?"):
            name = "limitless_decks.html"
        elif "/matchups" in path:
            name = "limitless_matchups.html"
        elif path.endswith("/decklist"):
            name = "limitless_decklist.html"
        else:
            name = "limitless_archetype.html"
        return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


def test_snapshot_filename_includes_date_and_format() -> None:
    """ファイル名に取得日とフォーマットが入る（docs/data-sources.md 4 節）。"""
    assert snapshot_filename("2026-09-07", "standard", "B4a") == "2026-09-07_B4a-standard.json"


def test_build_snapshot_records_metadata(client: FakeClient) -> None:
    """取得元 URL・取得日時・スキーマバージョンを含む。"""
    snapshot = build_snapshot(client, fmt="standard", card_set="B4a", top=2)

    assert snapshot["schema_version"] == 2
    assert "play.limitlesstcg.com" in snapshot["source_url"]
    assert snapshot["fetched_at"]
    assert snapshot["format"] == "standard"
    assert snapshot["set"] == "B4a"


def test_build_snapshot_limits_to_top_archetypes(client: FakeClient) -> None:
    """上位 N アーキタイプだけを取る。"""
    snapshot = build_snapshot(client, fmt="standard", card_set="B4a", top=2)
    assert len(snapshot["archetypes"]) == 2
    assert [a["rank"] for a in snapshot["archetypes"]] == [1, 2]


def test_build_snapshot_attaches_decklists(client: FakeClient) -> None:
    """各アーキタイプに代表デッキリストが付く。"""
    snapshot = build_snapshot(client, fmt="standard", card_set="B4a", top=1)
    top = snapshot["archetypes"][0]

    assert top["name"] == "Mega Lucario ex Lucario"
    assert top["share"] == pytest.approx(0.08299929261966517)
    assert "2 Riolu A2 91" in top["decklists"][0]


def test_build_snapshot_collects_multiple_decklists(client: FakeClient) -> None:
    """同一アーキタイプの構築違いを複数集める。

    代表 1 つだけでは構築のばらつきを無視してしまうため。
    """
    snapshot = build_snapshot(
        client, fmt="standard", card_set="B4a", top=1, decklists_per_archetype=4
    )
    assert len(snapshot["archetypes"][0]["decklists"]) == 4


def test_build_snapshot_passes_combine_flag(client: FakeClient) -> None:
    """派生を 1 アーキタイプに統合する指定を取得元に渡す。

    統合すると 1 アーキタイプあたりの試合数が増え、実戦勝率の誤差が縮む。
    """
    build_snapshot(client, fmt="standard", card_set="B3", top=1, combine=True)
    assert any("combine=1" in path for path in client.paths)


def test_build_snapshot_omits_combine_by_default(client: FakeClient) -> None:
    """既定では統合しない。"""
    build_snapshot(client, fmt="standard", card_set="B4a", top=1)
    assert all("combine" not in path for path in client.paths)


def test_build_snapshot_is_json_serializable(client: FakeClient) -> None:
    """そのままファイルに書ける。"""
    snapshot = build_snapshot(client, fmt="standard", card_set="B4a", top=1)
    assert json.loads(json.dumps(snapshot, ensure_ascii=False))


def test_build_snapshot_does_not_fetch_images(client: FakeClient) -> None:
    """カード画像は取得しない（docs/data-sources.md 5 節）。"""
    build_snapshot(client, fmt="standard", card_set="B4a", top=2)
    assert all(".png" not in path and ".jpg" not in path for path in client.paths)


def test_build_snapshot_attaches_real_matchups(client: FakeClient) -> None:
    """各アーキタイプに実戦の相性表が付く。方策の校正に使う。"""
    snapshot = build_snapshot(client, fmt="standard", card_set="B4a", top=1)
    matchups = snapshot["archetypes"][0]["matchups"]

    assert matchups
    assert matchups[0]["opponent"] == "Mega Lucario ex Lucario"
    assert matchups[0]["matches"] == 174
    assert matchups[0]["win_rate"] == pytest.approx(0.47126436781609193)


def test_latest_snapshot_path_picks_the_newest_file(tmp_path: Path) -> None:
    from pocket_api.ingest.snapshot import latest_snapshot_path

    for name in ("2026-09-07_B4a-standard.json", "2026-09-13_B4a-standard.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert latest_snapshot_path(tmp_path).name == "2026-09-13_B4a-standard.json"
    with pytest.raises(FileNotFoundError):
        latest_snapshot_path(tmp_path / "empty")
