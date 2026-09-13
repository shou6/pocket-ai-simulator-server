"""Bulbapedia からカードの和名（カード名・ワザ名・特性名）を集める。

対戦ログや相性表を日本語で追えるようにするためのもの。
deckgym-core のカード ID（`B3b 003` 形式）ごとに Bulbapedia のカードページを引き、
infobox の `ja name` とカードテキストの `jname` を取り出す。

取得は MediaWiki API（`action=query&prop=revisions`）で 50 ページずつまとめて行い、
`docs/data-sources.md` 2 節の原則（レート制限・キャッシュ・User-Agent）を守る。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

BASE_URL = "https://bulbapedia.bulbagarden.net"
"""取得元のベース URL。"""

API_PATH = "/w/api.php"

BATCH_SIZE = 50
"""1 回の API 呼び出しで問い合わせるページ数（MediaWiki の上限）。"""

EXPANSION_NAMES: dict[str, str] = {
    "A1": "Genetic Apex",
    "A1a": "Mythical Island",
    "A2": "Space-Time Smackdown",
    "A2a": "Triumphant Light",
    "A2b": "Shining Revelry",
    "A3": "Celestial Guardians",
    "A3a": "Extradimensional Crisis",
    "A3b": "Eevee Grove",
    "A4": "Wisdom of Sea and Sky",
    "A4a": "Secluded Springs",
    "A4b": "Deluxe Pack: ex",
    "B1": "Mega Rising",
    "B1a": "Crimson Blaze",
    "B2": "Fantastical Parade",
    "B2a": "Paldean Wonders",
    "B2b": "Mega Shine",
    "B3": "Pulsing Aura",
    "B3a": "Paradox Drive",
    "B3b": "Everyday Wonders",
    "B4": "Ruler of the Skies",
    "B4a": "Team Rocket's Ambition",
    "P-A": "Promo-A",
    "P-B": "Promo-B",
}
"""deckgym のセット記号から Bulbapedia の拡張名へ。deckgym の `booster_pack` と同じ名前。"""

_JA_NAME_RE = re.compile(r"^\|\s*ja name\s*=\s*(.*?)\s*$", re.MULTILINE)
_CARDTEXT_BLOCK_RE = re.compile(r"\{\{Cardtext/(Attack|Ability)/Pocket(.*?)\n\}\}", re.DOTALL)
_FIELD_RE = re.compile(r"^\|\s*(name|jname)\s*=\s*(.*?)\s*$", re.MULTILINE)
_ICON_RE = re.compile(r"\{\{TCGP Icon\|([^}]*)\}\}")
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
_TITLE_NUMBER_RE = re.compile(r" \((.+) (\d+)\)$")


class PageSource(Protocol):
    """ページを取得できるもの。テストではフィクスチャを返す実装に差し替える。"""

    def get(self, path: str, *, use_cache: bool = True) -> str: ...


@dataclass(frozen=True)
class CardNames:
    """1 枚のカードの和名。"""

    name: str
    attacks: dict[str, str] = field(default_factory=dict)
    abilities: dict[str, str] = field(default_factory=dict)


def page_title(card_id: str, name: str) -> str:
    """deckgym のカード ID と英名から Bulbapedia のページ名を組み立てる。

    別イラスト（番号違い）は元のページへリダイレクトされるので、番号はそのまま使う。
    """
    set_code, _, number = card_id.partition(" ")
    expansion = EXPANSION_NAMES.get(set_code)
    if expansion is None:
        msg = f"Bulbapedia の拡張名が分からないセット: {set_code}"
        raise ValueError(msg)
    return f"{name} ({expansion} {int(number)})"


_ICON_TEXT = {"ex": "ex", "Mega ex": "ex"}
"""アイコンの読み。日本語のカード名では「メガ」はカタカナで書かれ、M のロゴは ex だけ残す。"""


def _flatten(value: str) -> str:
    """`{{TCGP Icon|ex}}` のようなテンプレートを文字にする。"""
    value = _ICON_RE.sub(lambda m: _ICON_TEXT.get(m.group(1), m.group(1)), value)
    value = _TEMPLATE_RE.sub("", value).strip()
    # 「ツボツボ ex」のように ex の前に空白が入る書き方があるが、和名では詰める
    return re.sub(r"\s+ex$", "ex", value)


def parse_wikitext(text: str) -> CardNames | None:
    """カードページの wikitext から和名を取り出す。和名が無ければ None。"""
    match = _JA_NAME_RE.search(text)
    if match is None:
        return None
    name = _flatten(match.group(1))
    if not name:
        return None

    attacks: dict[str, str] = {}
    abilities: dict[str, str] = {}
    for kind, body in _CARDTEXT_BLOCK_RE.findall(text):
        fields = {key: _flatten(value) for key, value in _FIELD_RE.findall(body)}
        en, ja = fields.get("name"), fields.get("jname")
        if not en or not ja:
            continue
        (attacks if kind == "Attack" else abilities)[en] = ja
    return CardNames(name=name, attacks=attacks, abilities=abilities)


def parse_query_response(body: str) -> tuple[dict[str, CardNames], list[str]]:
    """API の応答を「問い合わせたページ名 → 和名」と「見つからなかったページ名」に分ける。

    リダイレクト先で見つかった和名は、問い合わせ元のページ名にも結び付ける。
    """
    data = json.loads(body)
    query = data.get("query", {})
    found: dict[str, CardNames] = {}
    missing: list[str] = []
    for page in query.get("pages", {}).values():
        title = page.get("title", "")
        if "missing" in page:
            missing.append(title)
            continue
        revisions = page.get("revisions") or []
        text = revisions[0].get("slots", {}).get("main", {}).get("*", "") if revisions else ""
        names = parse_wikitext(text)
        if names is None:
            missing.append(title)
        else:
            found[title] = names
    for redirect in query.get("redirects", []):
        target = found.get(redirect.get("to", ""))
        if target is not None:
            found[redirect["from"]] = target
    # MediaWiki は先頭を大文字にするなど正規化することがある
    for normalized in query.get("normalized", []):
        target = found.get(normalized.get("to", ""))
        if target is not None:
            found[normalized["from"]] = target
    return found, missing


def _query_path(titles: list[str]) -> str:
    joined = quote("|".join(titles), safe="")
    return (
        f"{API_PATH}?action=query&prop=revisions&rvprop=content&rvslots=main"
        f"&redirects=1&format=json&titles={joined}"
    )


def _search_path(name: str) -> str:
    # 本編 TCG の同名カードが大量に出るので、ポケポケのページに絞る
    term = quote(f'intitle:"{name}" "TCG Pocket"', safe="")
    return f"{API_PATH}?action=query&list=search&format=json&srlimit=10&srsearch={term}"


def _search_title(client: PageSource, name: str) -> str | None:
    """ページ名の推測が外れたカードを検索で探す。`{英名} ({拡張} {番号})` 形式だけ採用する。"""
    data = json.loads(client.get(_search_path(name)))
    for hit in data.get("query", {}).get("search", []):
        title = hit.get("title", "")
        if title.startswith(f"{name} (") and _TITLE_NUMBER_RE.search(title):
            return str(title)
    return None


def fetch_card_names(client: PageSource, cards: list[tuple[str, str]]) -> dict[str, CardNames]:
    """カード ID と英名の一覧から、和名を引けたものだけを返す。"""
    titles = {card_id: page_title(card_id, name) for card_id, name in cards}
    result: dict[str, CardNames] = {}

    unique_titles = sorted(set(titles.values()))
    found: dict[str, CardNames] = {}
    for start in range(0, len(unique_titles), BATCH_SIZE):
        batch = unique_titles[start : start + BATCH_SIZE]
        batch_found, _ = parse_query_response(client.get(_query_path(batch)))
        found.update(batch_found)

    unresolved: dict[str, list[str]] = {}
    for card_id, name in cards:
        names = found.get(titles[card_id])
        if names is not None:
            result[card_id] = names
        else:
            unresolved.setdefault(name, []).append(card_id)

    # 推測が外れたページは英名で検索し直す（別の拡張に元のページがある場合など）
    for name, card_ids in unresolved.items():
        title = _search_title(client, name)
        if title is None:
            continue
        page_found, _ = parse_query_response(client.get(_query_path([title])))
        names = page_found.get(title)
        if names is None:
            continue
        for card_id in card_ids:
            result[card_id] = names
    return result


def build_names_file(
    cards: list[tuple[str, str]],
    found: dict[str, CardNames],
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`data/cards/names_ja.json` に書く内容を組み立てる。

    `overrides` は `data/cards/name_overrides.json` の内容（カード ID → name / attacks /
    abilities）。
    Bulbapedia に無いカードや誤りを手動で補うためのもので、取得結果より優先する。
    """
    english = dict(cards)
    found = dict(found)
    for card_id, entry in (overrides or {}).items():
        if card_id not in english:
            continue
        found[card_id] = CardNames(
            name=entry["name"],
            attacks=dict(entry.get("attacks", {})),
            abilities=dict(entry.get("abilities", {})),
        )
    return {
        "schema_version": 1,
        "source": f"Bulbapedia ({BASE_URL}) のカードページ。CC BY-NC-SA 2.5",
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cards": {
            card_id: {
                "english": english[card_id],
                "name": names.name,
                "attacks": names.attacks,
                "abilities": names.abilities,
            }
            for card_id, names in sorted(found.items())
        },
        "missing": [card_id for card_id, _ in cards if card_id not in found],
    }
