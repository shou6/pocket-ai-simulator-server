"""メタデッキのスナップショットを組み立てる。

スナップショットは取得日とフォーマットでファイルを分け、上書きせず追記する
（`docs/data-sources.md` 4 節）。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pocket_engine_py as engine

from pocket_api.ingest.client import BASE_URL
from pocket_api.ingest.limitless import (
    Archetype,
    parse_archetypes,
    parse_decklist,
    parse_matchups,
)

SCHEMA_VERSION = 2
"""スナップショットのスキーマバージョン。構造を変えたら上げる。

2: decklist（1 つ）を decklists（複数）にし、combine を記録するようにした。
"""

_DECKLIST_LINK_RE = re.compile(r'href="(/tournament/[^"]+/decklist)"')


class PageSource(Protocol):
    """ページを取得できるもの。テストではフィクスチャを返す実装に差し替える。"""

    def get(self, path: str, *, use_cache: bool = True) -> str: ...


def latest_snapshot_path(meta_dir: Path) -> Path:
    """いちばん新しいスナップショットのファイル。ファイル名が日付から始まるので名前順の最後。

    Raises:
        FileNotFoundError: スナップショットが 1 つも無い場合。
    """
    files = sorted(meta_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"スナップショットがありません: {meta_dir}")
    return files[-1]


def snapshot_filename(date: str, fmt: str, card_set: str) -> str:
    """スナップショットのファイル名を組み立てる。

    Args:
        date: 取得日（`2026-09-07` 形式）。
        fmt: フォーマット（`standard` など）。
        card_set: 対象セット（`B4a` など）。

    Returns:
        `2026-09-07_B4a-standard.json` 形式のファイル名。
    """
    return f"{date}_{card_set}-{fmt}.json"


def _query(fmt: str, card_set: str, combine: bool) -> str:
    """一覧・相性表に共通のクエリ文字列。

    `combine` は派生デッキを 1 アーキタイプに統合する指定。統合すると
    1 アーキタイプあたりの試合数が増え、実戦勝率の信頼区間が縮む。
    """
    query = f"game=POCKET&format={fmt}&set={card_set}"
    return f"{query}&combine=1" if combine else query


def _meta_path(fmt: str, card_set: str, combine: bool) -> str:
    return f"/decks?{_query(fmt, card_set, combine)}"


def _archetype_path(slug: str, fmt: str, card_set: str, combine: bool) -> str:
    return f"/decks/{slug}?{_query(fmt, card_set, combine)}"


def _matchups_path(slug: str, fmt: str, card_set: str, combine: bool) -> str:
    return f"/decks/{slug}/matchups?{_query(fmt, card_set, combine)}"


def _fetch_matchups(
    source: PageSource, slug: str, fmt: str, card_set: str, combine: bool
) -> list[dict[str, Any]]:
    """アーキタイプの実戦相性表を取ってくる。

    総合勝率はアーキタイプ間の差が小さくノイズに埋もれるが、相性表は差が大きい。
    方策の選定と校正（フェーズ 3）の基準に使う。
    """
    page = source.get(_matchups_path(slug, fmt, card_set, combine))
    return [
        {
            "opponent": m.opponent,
            "opponent_slug": m.opponent_slug,
            "matches": m.matches,
            "wins": m.wins,
            "losses": m.losses,
            "ties": m.ties,
            "win_rate": m.win_rate,
        }
        for m in parse_matchups(page)
    ]


def _fetch_decklists(
    source: PageSource, slug: str, fmt: str, card_set: str, combine: bool, limit: int
) -> list[str]:
    """アーキタイプのデッキリストを上位から `limit` 件取ってくる。

    アーキタイプのページには上位入賞者のデッキリストへのリンクが並ぶ。
    同一アーキタイプでも構築には幅があるため、複数集めて平均を取れるようにする。

    登録者がエネルギーを指定していないデッキリストがあるため、
    エンジン側で `Energy:` 行を推定して補う。補えないものは捨てる。
    """
    page = source.get(_archetype_path(slug, fmt, card_set, combine))

    seen: set[str] = set()
    decklists: list[str] = []
    for link in _DECKLIST_LINK_RE.finditer(page):
        path = link.group(1)
        if path in seen:
            continue
        seen.add(path)

        try:
            text = parse_decklist(source.get(path))
            decklists.append(str(engine.normalize_deck(text)))
        except ValueError:
            continue
        if len(decklists) >= limit:
            break
    return decklists


def _as_dict(
    archetype: Archetype, decklists: list[str], matchups: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "rank": archetype.rank,
        "name": archetype.name,
        "slug": archetype.slug,
        "count": archetype.count,
        "share": archetype.share,
        "wins": archetype.wins,
        "losses": archetype.losses,
        "ties": archetype.ties,
        "win_rate": archetype.win_rate,
        "decklists": decklists,
        "matchups": matchups,
    }


def build_snapshot(
    source: PageSource,
    *,
    fmt: str,
    card_set: str,
    top: int,
    combine: bool = False,
    decklists_per_archetype: int = 1,
) -> dict[str, Any]:
    """上位 `top` アーキタイプのスナップショットを組み立てる。

    Args:
        source: ページの取得元。
        fmt: フォーマット（`standard` など）。
        card_set: 対象セット（`B4a` など）。
        top: 取得する上位アーキタイプ数。
        combine: 派生デッキを 1 アーキタイプに統合するか。
        decklists_per_archetype: 1 アーキタイプあたりに集めるデッキリスト数。

    Returns:
        JSON にそのまま書ける辞書。

    Raises:
        ValueError: 取得元の構造が変わり、解釈できない場合。
    """
    meta_path = _meta_path(fmt, card_set, combine)
    archetypes = parse_archetypes(source.get(meta_path))[:top]

    rows = [
        _as_dict(
            archetype,
            _fetch_decklists(
                source, archetype.slug, fmt, card_set, combine, decklists_per_archetype
            ),
            _fetch_matchups(source, archetype.slug, fmt, card_set, combine),
        )
        for archetype in archetypes
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "source_url": f"{BASE_URL}{meta_path}",
        "fetched_at": datetime.now(UTC).isoformat(),
        "format": fmt,
        "set": card_set,
        "combine": combine,
        "archetypes": rows,
    }
