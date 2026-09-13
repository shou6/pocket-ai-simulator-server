"""デッキのアーキタイプ（どういうデッキか）を判定する。

`docs/requirements.md` 8 章の最後「limitless の分類を正解としてルール化する」の実装。
limitless のアーキタイプ名は、そのデッキの主要なポケモンの名前を並べたもの
（`Mega Lucario ex Lucario`）。そこで名前をカード名に分解し、
「この組み合わせを含むデッキ」と定義する。

1. **環境のアーキタイプに当てる**：名前に出てくるポケモンをすべて含めば、そのアーキタイプ。
   複数に当たるときは、名前に出てくるポケモンが多いほう（より絞り込まれた定義）を採る
2. **当たらなければ名前を作る**：デッキの主要なポケモンを limitless と同じ流儀で 2 体まで並べる

判定はカードの名前だけで行う。再録やレアリティ違いは同じ名前なので同一視される。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

import pocket_engine_py as engine

from pocket_api.optimize.recipe import DeckRecipe

MAX_NAMED = 2
"""作る名前に並べるポケモンの数。limitless の名前も 1〜2 体。"""


@dataclass(frozen=True)
class Archetype:
    """判定の結果。"""

    name: str
    """英名のアーキタイプ名。環境に無いデッキは、主要なポケモンから作った名前。"""

    slug: str | None
    """環境のアーキタイプに当たったときの limitless の識別子。"""

    in_meta: bool
    """環境のアーキタイプに当たったか。"""

    key_cards: tuple[str, ...]
    """名前の元にしたカード ID（デッキに入っている印刷）。"""


@dataclass(frozen=True)
class MetaArchetype:
    """環境のアーキタイプの定義。名前に出てくるポケモンの名前の組。"""

    name: str
    slug: str
    signature: frozenset[str]


@dataclass(frozen=True)
class _Card:
    id: str
    name: str
    kind: str
    stage: int
    evolves_from: str | None
    hp: int


@cache
def _details(card_id: str) -> _Card | None:
    found = engine.card_details([card_id])
    if not found:
        return None
    card = found[0]
    return _Card(
        id=card.id,
        name=card.name,
        kind=card.kind,
        stage=card.stage or 0,
        evolves_from=card.evolves_from,
        hp=card.hp or 0,
    )


def _pokemon(recipe: DeckRecipe) -> list[tuple[_Card, int]]:
    rows: list[tuple[_Card, int]] = []
    for card_id, count in recipe.cards:
        card = _details(card_id)
        if card is not None and card.kind == "ポケモン":
            rows.append((card, count))
    return rows


def split_archetype_name(name: str, pokemon_names: Sequence[str]) -> frozenset[str]:
    """アーキタイプ名を、そこに出てくるポケモンの名前に分解する。

    長い名前から順に拾い、拾った部分は取り除く（`Mega Lucario ex Lucario` の
    `Lucario` を `Mega Lucario ex` の一部として二重に数えない）。
    """
    rest = f" {name} "
    found: set[str] = set()
    for candidate in sorted(set(pokemon_names), key=len, reverse=True):
        needle = f" {candidate} "
        while needle in rest:
            found.add(candidate)
            rest = rest.replace(needle, " ", 1)
    return frozenset(found)


def meta_archetypes(archetypes: Sequence[tuple[str, str, Sequence[str]]]) -> list[MetaArchetype]:
    """（名前, slug, デッキリスト）から、環境のアーキタイプの定義を作る。

    名前の分解には、そのアーキタイプのデッキリストに入っているポケモンの名前を使う。
    """
    built: list[MetaArchetype] = []
    for name, slug, decklists in archetypes:
        names: set[str] = set()
        for text in decklists:
            names |= {card.name for card, _ in _pokemon(DeckRecipe.from_text(text))}
        signature = split_archetype_name(name, sorted(names))
        if signature:
            built.append(MetaArchetype(name=name, slug=slug, signature=signature))
    return built


def _is_rule_box(card: _Card) -> bool:
    return card.name.endswith(" ex")


def key_pokemon(recipe: DeckRecipe, limit: int = MAX_NAMED) -> list[_Card]:
    """デッキの主要なポケモン。limitless の名付けに合わせて選ぶ。

    - デッキ内で進化先があるポケモン（リオル → ルカリオのリオル）は候補にしない
    - ex（メガシンカを含む）を先に、次に進化したポケモン、枚数、HP の順
    """
    rows = _pokemon(recipe)
    evolved_from = {card.evolves_from for card, _ in rows if card.evolves_from}
    finals = [(card, count) for card, count in rows if card.name not in evolved_from]

    def rank(item: tuple[_Card, int]) -> tuple[int, int, int, int, int, str]:
        card, count = item
        return (
            -int(_is_rule_box(card)),
            -int(card.name.startswith("Mega ")),
            -int(card.stage > 0),
            -count,
            -card.hp,
            card.id,
        )

    picked: list[_Card] = []
    for card, _ in sorted(finals, key=rank):
        if card.name in {p.name for p in picked}:
            continue
        picked.append(card)
        if len(picked) >= limit:
            break
    return picked


def classify(recipe: DeckRecipe, meta: Sequence[MetaArchetype]) -> Archetype:
    """デッキのアーキタイプ。"""
    rows = _pokemon(recipe)
    names = {card.name for card, _ in rows}
    by_name = {card.name: card.id for card, _ in rows}
    matches = [archetype for archetype in meta if archetype.signature <= names]
    if matches:
        best = max(matches, key=lambda a: len(a.signature))
        return Archetype(
            name=best.name,
            slug=best.slug,
            in_meta=True,
            key_cards=tuple(by_name[name] for name in sorted(best.signature)),
        )
    keys = key_pokemon(recipe)
    return Archetype(
        name=" ".join(card.name for card in keys) or "ポケモンなし",
        slug=None,
        in_meta=False,
        key_cards=tuple(card.id for card in keys),
    )
