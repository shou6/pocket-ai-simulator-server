"""limitless（play.limitlesstcg.com）のページを解釈する。

取得は行わず、渡された HTML を解釈するだけに徹する。取得は
`LimitlessClient`（同ディレクトリ）が担う。

取得元の構造が変わったら黙って空を返さず例外にする。
スナップショットが静かに壊れるのを防ぐため（`docs/data-sources.md` 2 節）。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

# メタ表の 1 行。data-share と data-winrate に生の数値が入っている
_ROW_RE = re.compile(
    r'<tr data-share="(?P<share>[^"]+)" data-winrate="(?P<winrate>[^"]+)">(?P<cells>.*?)</tr>',
    re.DOTALL,
)
_TABLE_RE = re.compile(r'<table class="meta">.*?</table>', re.DOTALL)
_RANK_RE = re.compile(r"<td>(\d+)</td>")
_LINK_RE = re.compile(r'<td><a href="/decks/(?P<slug>[^?"]+)\?[^"]*">(?P<name>[^<]+)</a></td>')
_COUNT_RE = re.compile(r'<td class="landscape-only">(\d+)</td>')
_SCORE_RE = re.compile(r">(?P<wins>\d+) - (?P<losses>\d+) - (?P<ties>\d+)<")

# 相性表の 1 行。data-name / data-matches / data-winrate に生の値が入っている
_MATCHUP_ROW_RE = re.compile(
    r'<tr data-name="(?P<name>[^"]*)" data-matches="(?P<matches>\d+)"'
    r' data-winrate="(?P<winrate>[^"]+)">(?P<cells>.*?)</tr>',
    re.DOTALL,
)
_MATCHUP_SLUG_RE = re.compile(r'href="/decks/(?P<slug>[^/"?]+)/matchups')

# 個別デッキリストのページに、エンジンが読める形式の文字列が埋め込まれている
_DECKLIST_RE = re.compile(r"const decklist = `(?P<text>[^`]+)`")


@dataclass(frozen=True)
class Archetype:
    """メタ表の 1 行。"""

    rank: int
    """使用率順の順位。"""

    name: str
    """アーキタイプ名（英語）。"""

    slug: str
    """limitless の URL に使われる識別子。"""

    count: int
    """使用されたデッキ数。"""

    share: float
    """使用率（0.0〜1.0）。"""

    wins: int
    """勝ち数。"""

    losses: int
    """負け数。"""

    ties: int
    """引き分け数。"""

    win_rate: float
    """勝率（0.0〜1.0）。"""


def parse_archetypes(page: str) -> list[Archetype]:
    """メタ表のページからアーキタイプの一覧を取り出す。

    Args:
        page: `/decks?game=POCKET` のページの HTML。

    Returns:
        使用率順のアーキタイプ一覧。

    Raises:
        ValueError: メタ表が見つからない、または行を解釈できない場合。
    """
    table = _TABLE_RE.search(page)
    if table is None:
        raise ValueError("メタ表が見つかりません。取得元の構造が変わった可能性があります")

    archetypes: list[Archetype] = []
    for row in _ROW_RE.finditer(table.group(0)):
        cells = row.group("cells")

        rank = _RANK_RE.search(cells)
        link = _LINK_RE.search(cells)
        count = _COUNT_RE.search(cells)
        score = _SCORE_RE.search(cells)
        if rank is None or link is None or count is None or score is None:
            raise ValueError(f"メタ表の行を解釈できません: {cells[:120]}")

        archetypes.append(
            Archetype(
                rank=int(rank.group(1)),
                name=html.unescape(link.group("name")).strip(),
                slug=link.group("slug"),
                count=int(count.group(1)),
                share=float(row.group("share")),
                wins=int(score.group("wins")),
                losses=int(score.group("losses")),
                ties=int(score.group("ties")),
                win_rate=float(row.group("winrate")),
            )
        )

    if not archetypes:
        raise ValueError("メタ表に行がありません。取得元の構造が変わった可能性があります")
    return archetypes


_DECK_LINK_SET_RE = re.compile(r'href="/decks/[^"?]+\?[^"]*?set=(?P<set>[A-Za-z0-9-]+)')


def parse_displayed_set(page: str) -> str:
    """一覧ページが表示しているセット。セットを指定せずに開くと、いまの既定（最新）のセットになる。

    アーキタイプへのリンクに付いている `set=` のうち、いちばん多いものを採る。

    Raises:
        ValueError: リンクが見つからない場合。
    """
    found = [match.group("set") for match in _DECK_LINK_SET_RE.finditer(page)]
    if not found:
        raise ValueError("表示しているセットが分かりません。取得元の構造が変わった可能性があります")
    return max(set(found), key=found.count)


def parse_decklist(page: str) -> str:
    """個別のデッキリストページからデッキテキストを取り出す。

    返す形式は deckgym がそのまま読める `2 Riolu A2 91` 形式の行と
    `Energy: Fighting` 行からなる文字列。

    Args:
        page: `/tournament/<id>/player/<name>/decklist` のページの HTML。

    Returns:
        エンジンに渡せるデッキテキスト。

    Raises:
        ValueError: デッキリストが見つからない場合。
    """
    match = _DECKLIST_RE.search(page)
    if match is None:
        raise ValueError("デッキリストが見つかりません。取得元の構造が変わった可能性があります")
    return html.unescape(match.group("text"))


@dataclass(frozen=True)
class Matchup:
    """相性表の 1 行。ある 1 デッキから見た、特定の相手との実戦成績。"""

    opponent: str
    """相手アーキタイプ名（英語）。"""

    opponent_slug: str
    """相手アーキタイプの識別子。"""

    matches: int
    """試合数。"""

    wins: int
    """勝ち数。"""

    losses: int
    """負け数。"""

    ties: int
    """引き分け数。"""

    win_rate: float
    """勝率（0.0〜1.0）。"""


def parse_matchups(page: str) -> list[Matchup]:
    """相性表のページから、相手ごとの成績を取り出す。

    総合勝率はアーキタイプ間の差が小さくノイズに埋もれるが、
    相性表は差が大きいので方策の校正に使える。

    Args:
        page: `/decks/<slug>/matchups?game=POCKET` のページの HTML。

    Returns:
        相手ごとの成績。試合数の多い順（取得元の並び順のまま）。

    Raises:
        ValueError: 相性表が見つからない、または行を解釈できない場合。
    """
    table = re.search(r"<table[^>]*>.*?</table>", page, re.DOTALL)
    if table is None:
        raise ValueError("相性表が見つかりません。取得元の構造が変わった可能性があります")

    matchups: list[Matchup] = []
    for row in _MATCHUP_ROW_RE.finditer(table.group(0)):
        cells = row.group("cells")
        slug = _MATCHUP_SLUG_RE.search(cells)
        if slug is None:
            # アーキタイプに分類できなかった相手（Unknown 行）。集計に使えないので飛ばす
            continue

        score = _SCORE_RE.search(cells)
        if score is None:
            raise ValueError(f"相性表の行を解釈できません: {cells[:120]}")

        matchups.append(
            Matchup(
                opponent=html.unescape(row.group("name")).strip(),
                opponent_slug=slug.group("slug"),
                matches=int(row.group("matches")),
                wins=int(score.group("wins")),
                losses=int(score.group("losses")),
                ties=int(score.group("ties")),
                win_rate=float(row.group("winrate")),
            )
        )

    if not matchups:
        raise ValueError("相性表に行がありません。取得元の構造が変わった可能性があります")
    return matchups
