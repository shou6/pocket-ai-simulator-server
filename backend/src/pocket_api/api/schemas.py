"""複数のルーターで共有するレスポンスの型。"""

from __future__ import annotations

from pydantic import BaseModel

from pocket_api.cards.catalog import CardView, DeckView, card_view, deck_view, translator
from pocket_api.optimize.archetype import Archetype
from pocket_api.optimize.recipe import DeckRecipe


class CardOut(BaseModel):
    """カード 1 枚。和名を主に、英名を併記できるように両方返す。"""

    id: str
    name: str
    name_ja: str
    kind: str
    energy_type: str | None
    stage: int | None
    hp: int | None

    @classmethod
    def of(cls, card: CardView) -> CardOut:
        return cls(
            id=card.id,
            name=card.name,
            name_ja=card.name_ja,
            kind=card.kind,
            energy_type=card.energy_type,
            stage=card.stage,
            hp=card.hp,
        )


class DeckCardOut(BaseModel):
    """デッキ内の 1 種類。"""

    card: CardOut
    count: int


class DeckOut(BaseModel):
    """デッキ。`decklist` はエンジンに渡せるテキストで、コピーにもそのまま使う。"""

    decklist: str
    energy: list[str]
    energy_ja: list[str]
    cards: list[DeckCardOut]
    size: int

    @classmethod
    def of(cls, view: DeckView) -> DeckOut:
        return cls(
            decklist=view.decklist,
            energy=list(view.energy),
            energy_ja=list(view.energy_ja),
            cards=[DeckCardOut(card=CardOut.of(row.card), count=row.count) for row in view.cards],
            size=view.size,
        )

    @classmethod
    def from_recipe(cls, recipe: DeckRecipe) -> DeckOut:
        return cls.of(deck_view(recipe))

    @classmethod
    def from_text(cls, decklist: str) -> DeckOut:
        return cls.from_recipe(DeckRecipe.from_text(decklist))


class Precision(BaseModel):
    """結果の確からしさ。差を裸で出さないために、どのレスポンスにも添える。"""

    strategy: str
    games: int
    noise: float
    """1 枚違いのデッキどうしの差のぶれ（標準偏差）。`improve_cli.delta_noise`。"""


class ArchetypeOut(BaseModel):
    """デッキのアーキタイプ（`optimize.archetype.classify`）。"""

    name: str
    name_ja: str
    slug: str | None
    in_meta: bool
    """環境のアーキタイプに当たったか。当たらなければ主要なポケモンから作った名前。"""

    key_cards: list[CardOut]

    @classmethod
    def of(cls, archetype: Archetype) -> ArchetypeOut:
        cards = [view for view in (card_view(cid) for cid in archetype.key_cards) if view]
        name_ja = (
            translator().text(archetype.name)
            if archetype.in_meta
            else " ".join(card.name_ja for card in cards) or archetype.name
        )
        return cls(
            name=archetype.name,
            name_ja=name_ja,
            slug=archetype.slug,
            in_meta=archetype.in_meta,
            key_cards=[CardOut.of(card) for card in cards],
        )
