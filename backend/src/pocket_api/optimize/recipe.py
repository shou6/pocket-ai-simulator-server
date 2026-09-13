"""デッキの表現と制約。

探索ではデッキを「カード ID → 枚数」の多重集合として扱う。テキストとの相互変換で
エンジンに渡す。制約は `docs/requirements.md` 8 章のとおり
20 枚・同名 2 枚以内・たねポケモン 1 枚以上・所持カード。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import pocket_engine_py as engine

from pocket_api.optimize.conditions import (
    JAPANESE_ENERGY,
    DeckPokemon,
    Requirement,
    requirement_of,
    unmet,
)

DECK_SIZE = 20
"""デッキの枚数。"""

MAX_SAME_NAME = 2
"""同名カードの上限。同じ名前なら別セットでも合算する。"""

_LINE = re.compile(r"^(\d+)\s+(P-[AB]|[AB]\d[a-z]?)\s+(\d+)$")


@dataclass(frozen=True)
class Ownership:
    """所持カード。カード ID ごとの所持枚数。"""

    counts: dict[str, int]

    def owned(self, card_id: str) -> int:
        """その ID を何枚持っているか。"""
        return self.counts.get(card_id, 0)


@dataclass(frozen=True)
class DeckRecipe:
    """デッキの中身。カード ID と枚数の組を、ID 順に並べて持つ。"""

    cards: tuple[tuple[str, int], ...]
    """（カード ID, 枚数）。枚数が 0 のものは持たない。"""

    energy: tuple[str, ...]
    """エネルギータイプ（英名）。"""

    @property
    def size(self) -> int:
        """デッキの枚数。"""
        return sum(count for _, count in self.cards)

    def count_of(self, card_id: str) -> int:
        """その ID が何枚入っているか。"""
        return dict(self.cards).get(card_id, 0)

    def with_change(self, card_id: str, delta: int) -> DeckRecipe:
        """1 種類の枚数を増減した新しいデッキ。探索の 1 手に使う。"""
        counts = dict(self.cards)
        updated = counts.get(card_id, 0) + delta
        if updated < 0:
            raise ValueError(f"枚数を負にはできません: {card_id} が {updated} 枚")
        if updated == 0:
            counts.pop(card_id, None)
        else:
            counts[card_id] = updated
        return DeckRecipe(cards=tuple(sorted(counts.items())), energy=self.energy)

    def to_text(self) -> str:
        """エンジンに渡すデッキテキスト。"""
        lines = [f"Energy: {', '.join(self.energy)}"]
        lines += [f"{count} {card_id}" for card_id, count in self.cards]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_text(cls, decklist: str) -> DeckRecipe:
        """デッキテキストから作る。書き方の違いは吸収する。"""
        counts: dict[str, int] = {}
        energy: tuple[str, ...] = ()
        for raw in decklist.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.lower().startswith("energy:"):
                energy = tuple(
                    part.strip() for part in line.split(":", 1)[1].split(",") if part.strip()
                )
                continue
            match = _LINE.match(line)
            if match is None:
                # 「2 Caterpie B3b 1」のように名前が入る書式にも合わせる
                parts = line.split()
                if len(parts) >= 3 and parts[0].isdigit() and parts[-1].isdigit():
                    count, set_code, number = int(parts[0]), parts[-2], int(parts[-1])
                else:
                    raise ValueError(f"読み取れない行です: {line}")
            else:
                count, set_code, number = int(match.group(1)), match.group(2), int(match.group(3))
            card_id = f"{set_code} {number:03d}"
            counts[card_id] = counts.get(card_id, 0) + count
        return cls(cards=tuple(sorted(counts.items())), energy=energy)


@lru_cache(maxsize=4096)
def _card_info(card_id: str) -> tuple[str, bool]:
    """カードの（名前, たねポケモンか）。見つからなければ ID をそのまま名前にする。"""
    found = engine.card_details([card_id])
    if not found:
        return card_id, False
    card = found[0]
    return card.name, card.kind == "ポケモン" and card.stage == 0


@lru_cache(maxsize=4096)
def _card_requirement(card_id: str) -> tuple[Requirement, DeckPokemon | None]:
    """そのカードが働くための条件と、ポケモンならその性質。カードごとに一度だけ調べる。

    デッキの検証は候補ごとに何度も呼ばれるので、カード単位で覚えておかないと重い。
    """
    found = engine.card_details([card_id])
    if not found:
        return Requirement(), None
    card = found[0]
    if card.kind == "ポケモン":
        return Requirement(), DeckPokemon(
            name=card.name,
            energy_type=card.energy_type,
            stage=card.stage or 0,
            lineage=card.lineage,
        )
    return requirement_of(card.effect or ""), None


def _dead_cards(recipe: DeckRecipe) -> list[str]:
    """そのデッキで働かないカードの指摘。"""
    energies = {JAPANESE_ENERGY.get(name, name) for name in recipe.energy}
    pokemon = [info for card_id, _ in recipe.cards if (info := _card_requirement(card_id)[1])]
    problems: list[str] = []
    for card_id, _ in recipe.cards:
        requirement, _info = _card_requirement(card_id)
        if requirement.empty:
            continue
        reason = unmet(requirement, pokemon, energies)
        if reason is not None:
            problems.append(f"{_card_info(card_id)[0]} は{reason}")
    return problems


def violations(
    recipe: DeckRecipe, ownership: Ownership | None = None, *, partial: bool = False
) -> tuple[str, ...]:
    """満たしていない制約を日本語で並べる。すべて満たしていれば空。

    `partial` は組み立て途中のデッキを見るときに使い、枚数とたねの有無を問わない。
    """
    found: list[str] = []
    if not partial and recipe.size != DECK_SIZE:
        found.append(f"デッキは {DECK_SIZE} 枚である必要があります（現在 {recipe.size} 枚）")
    if not recipe.energy:
        found.append("エネルギータイプが指定されていません")

    by_name: dict[str, int] = {}
    basics = 0
    for card_id, count in recipe.cards:
        name, is_basic = _card_info(card_id)
        by_name[name] = by_name.get(name, 0) + count
        if is_basic:
            basics += count
    for name, count in sorted(by_name.items()):
        if count > MAX_SAME_NAME:
            found.append(f"同名のカードは {MAX_SAME_NAME} 枚までです（{name} が {count} 枚）")
    if basics == 0 and not partial:
        found.append("たねポケモンが 1 枚もありません")

    # そのデッキで働かないカードは入れても意味がない。
    # プールの制限は「新しく追加する候補」にしか効かないので、交叉で混ざったものは
    # ここで弾く（ユーザーの指摘：闘デッキと超デッキが混ざり、コルニだけ残っていた）
    found.extend(_dead_cards(recipe))

    if ownership is not None:
        for card_id, count in recipe.cards:
            owned = ownership.owned(card_id)
            if count > owned:
                name, _ = _card_info(card_id)
                found.append(
                    f"所持しているより多く入っています（{name} を {count} 枚、所持 {owned} 枚）"
                )
    return tuple(found)
