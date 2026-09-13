"""画面に出すためのカード情報。

エンジンのカード詳細（英名）に、和名（`data/cards/names_ja.json`）と
デッキコードの番号を添える。API はここから組み立て、表示用の整形をルーターに書かない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache

import pocket_engine_py as engine

from pocket_api.cards.deck_builder_ids import deck_builder_ids
from pocket_api.cards.japanese import DEFAULT_NAMES_PATH, Translator
from pocket_api.optimize.conditions import JAPANESE_ENERGY
from pocket_api.optimize.recipe import DeckRecipe


@dataclass(frozen=True)
class CardView:
    """画面に出す 1 枚ぶんの情報。"""

    id: str
    name: str
    """英名（エンジンの名前）。"""

    name_ja: str
    """和名。無ければ英名。"""

    kind: str
    energy_type: str | None
    stage: int | None
    hp: int | None
    implemented: bool


@cache
def _names_ja() -> dict[str, str]:
    if not DEFAULT_NAMES_PATH.exists():
        return {}
    data = json.loads(DEFAULT_NAMES_PATH.read_text(encoding="utf-8"))
    return {card_id: entry["name"] for card_id, entry in data["cards"].items()}


@cache
def translator() -> Translator:
    """アーキタイプ名など、文中の英名を和名にする。"""
    return Translator.from_file()


@cache
def _implemented() -> frozenset[str]:
    return frozenset(c.id for c in engine.card_statuses_all() if c.is_complete)


@cache
def all_cards() -> tuple[CardView, ...]:
    """エンジンが知っているすべての印刷。"""
    ids = [c.id for c in engine.card_statuses_all()]
    names = _names_ja()
    implemented = _implemented()
    return tuple(
        CardView(
            id=card.id,
            name=card.name,
            name_ja=names.get(card.id, card.name),
            kind=card.kind,
            energy_type=card.energy_type,
            stage=card.stage,
            hp=card.hp,
            implemented=card.id in implemented,
        )
        for card in engine.card_details(ids)
    )


@cache
def _by_id() -> dict[str, CardView]:
    return {card.id: card for card in all_cards()}


def card_view(card_id: str) -> CardView | None:
    """そのカードの表示用情報。知らない ID なら `None`。"""
    return _by_id().get(card_id)


def search_cards(query: str, *, limit: int = 30) -> list[CardView]:
    """名前（和名・英名）の部分一致で探す。

    再録やレアリティ違いは同じ機能カードなので、デッキコードの番号でまとめて
    代表の 1 枚だけ返す。未実装のカードは軸にできないので返さない。
    """
    needle = query.strip().lower()
    if not needle:
        return []
    ids = deck_builder_ids()
    seen: set[tuple[str, int] | str] = set()
    found: list[CardView] = []
    for card in all_cards():
        if not card.implemented:
            continue
        if needle not in card.name_ja.lower() and needle not in card.name.lower():
            continue
        key = ids.key_of(card.id) or card.id
        if key in seen:
            continue
        seen.add(key)
        representative = ids.representative(*key) if isinstance(key, tuple) else card.id
        found.append(card_view(representative or card.id) or card)
    # 短い名前（完全一致に近いもの）を先に出す
    found.sort(key=lambda c: (len(c.name_ja), c.name_ja, c.id))
    return found[:limit]


@dataclass(frozen=True)
class DeckCard:
    """デッキ内の 1 種類。"""

    card: CardView
    count: int


@dataclass(frozen=True)
class DeckView:
    """画面に出すデッキ。"""

    decklist: str
    energy: tuple[str, ...]
    energy_ja: tuple[str, ...]
    cards: tuple[DeckCard, ...]
    size: int


_KIND_ORDER = {"ポケモン": 0, "グッズ": 1, "ポケモンのどうぐ": 2, "サポート": 3, "スタジアム": 4}


def deck_view(recipe: DeckRecipe) -> DeckView:
    """デッキを表示用にする。ポケモン → トレーナーズの順に並べる。"""
    rows: list[DeckCard] = []
    for card_id, count in recipe.cards:
        view = card_view(card_id) or CardView(
            id=card_id,
            name=card_id,
            name_ja=card_id,
            kind="不明",
            energy_type=None,
            stage=None,
            hp=None,
            implemented=False,
        )
        rows.append(DeckCard(card=view, count=count))
    rows.sort(key=lambda row: (_KIND_ORDER.get(row.card.kind, 9), row.card.stage or 0, row.card.id))
    return DeckView(
        decklist=recipe.to_text(),
        energy=recipe.energy,
        energy_ja=tuple(JAPANESE_ENERGY.get(name, name) for name in recipe.energy),
        cards=tuple(rows),
        size=recipe.size,
    )
