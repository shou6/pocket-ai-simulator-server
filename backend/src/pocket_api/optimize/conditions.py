"""カードが「このデッキで働くか」の判定。

効果に条件が付いたカードがある。条件を満たさないデッキに入れても何も起きない。

- タイプ：コルニ「自分の [F] ポケモン」、どうぐ「この札を付けた [M] ポケモン」、
  おさかなネット「トラッシュの [W] ポケモン」など
- エネルギー：カイ「[W] エネルギーが付いているポケモン」
- 進化段階：大きなふうせん「2 進化ポケモン」、ゆうがなマント「1 進化ポケモン」
- 古代・未来：ブーストエナジー、オーリム博士、フトゥー博士
- メガシンカ ex：セレナ
- カード名：ネモ「自分のパーモット」

`recipe`（制約検証）と `search`（候補プール）の両方から使うので、
どちらにも依存しない場所に置く。

2026-09-13 まで「your [X]」の形しか読めず、どうぐや山札を探すカードの条件を見落としていた。
代わりのカードに、悪デッキでは働かないメタルコアバリアやウォーターボートが並んでいた。
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from functools import cache

import pocket_engine_py as engine

# エネルギータイプの英名から、カード情報で使われている和名への対応
JAPANESE_ENERGY = {
    "Grass": "草",
    "Fire": "炎",
    "Water": "水",
    "Lightning": "雷",
    "Psychic": "超",
    "Fighting": "闘",
    "Darkness": "悪",
    "Metal": "鋼",
    "Dragon": "竜",
    "Colorless": "無",
}

_TYPE_LETTERS = {
    "G": "草",
    "R": "炎",
    "W": "水",
    "L": "雷",
    "P": "超",
    "F": "闘",
    "D": "悪",
    "M": "鋼",
    "N": "竜",
    "C": "無",
}

_TYPE_PATTERNS = (
    # 自分の（バトル場・ベンチの）[X] ポケモン
    re.compile(r"your (?:Active |Benched )?\[(\w)\] Pokémon"),
    # どうぐ：この札を付けた [X] ポケモン
    re.compile(r"[Tt]he \[(\w)\] Pokémon this card is attached to"),
    # 山札・トラッシュの [X] ポケモンを探す
    re.compile(r"\[(\w)\] Pokémon (?:is chosen at random )?from your (?:deck|discard pile)"),
    re.compile(r"is a \[(\w)\] Pokémon"),
    # 場の [X] ポケモンすべてに効くスタジアム
    re.compile(r"each \[(\w)\] Pokémon in play"),
)
_ENERGY_PATTERN = re.compile(r"any \[(\w)\] Energy attached")
_STAGE_PATTERN = re.compile(r"Stage (\d) Pokémon")
_LINEAGE_PATTERN = re.compile(r"(Ancient|Future) Pokémon")
_MEGA_SEARCH = re.compile(r"Mega Evolution Pokémon ex from your deck")
_AS_IF_IT_WERE = re.compile(r"as if it were [^.]*\.")
"""化石の「この札を 40 HP の [C] ポケモンとして出す」。条件ではない。"""

_LINEAGES = {"Ancient": "古代", "Future": "未来"}

_OPPONENT_BEFORE = re.compile(r"opponent's (?:Active |Benched )?$")
"""記述の直前が相手（`your opponent's Active`）なら相手のポケモンの話。"""


@dataclass(frozen=True)
class Requirement:
    """カードが働くための条件。すべて空なら条件なし。"""

    types: frozenset[str] = field(default_factory=frozenset)
    """このタイプ（和名 1 字）のポケモンのどれかが要る。"""

    energies: frozenset[str] = field(default_factory=frozenset)
    """このタイプのエネルギーのどれかをデッキで使う必要がある。"""

    stages: frozenset[int] = field(default_factory=frozenset)
    """この進化段階のポケモンのどれかが要る。"""

    lineages: frozenset[str] = field(default_factory=frozenset)
    """古代・未来のポケモンが要る。"""

    mega_ex: bool = False
    """メガシンカ ex が要る。"""

    pokemon: frozenset[str] = field(default_factory=frozenset)
    """名指しされたポケモン（英名）のどれかが要る。"""

    @property
    def empty(self) -> bool:
        return not (
            self.types
            or self.energies
            or self.stages
            or self.lineages
            or self.mega_ex
            or self.pokemon
        )


@dataclass(frozen=True)
class DeckPokemon:
    """条件の判定に使う、デッキのポケモンの性質。"""

    name: str
    energy_type: str | None
    stage: int
    lineage: str | None = None


def _ours(effect: str, start: int) -> bool:
    """その位置の記述が自分の側を指すか。直前に相手（opponent's）が来ていれば相手の話。"""
    return _OPPONENT_BEFORE.search(effect[max(0, start - 30) : start]) is None


@cache
def requirement_of(effect: str) -> Requirement:
    """効果文から、カードが働くための条件を読む。"""
    text = _AS_IF_IT_WERE.sub("", effect)
    types = {
        _TYPE_LETTERS[match.group(1).upper()]
        for pattern in _TYPE_PATTERNS
        for match in pattern.finditer(text)
        if match.group(1).upper() in _TYPE_LETTERS and _ours(text, match.start())
    }
    energies = {
        _TYPE_LETTERS[match.group(1).upper()]
        for match in _ENERGY_PATTERN.finditer(text)
        if match.group(1).upper() in _TYPE_LETTERS and _ours(text, match.start())
    }
    stages = {int(m.group(1)) for m in _STAGE_PATTERN.finditer(text) if _ours(text, m.start())}
    lineages = {
        _LINEAGES[m.group(1)] for m in _LINEAGE_PATTERN.finditer(text) if _ours(text, m.start())
    }
    return Requirement(
        types=frozenset(types),
        energies=frozenset(energies),
        stages=frozenset(stages),
        lineages=frozenset(lineages),
        mega_ex=bool(_MEGA_SEARCH.search(text)),
        pokemon=required_pokemon_of(effect),
    )


def unmet(
    requirement: Requirement, pokemon: Iterable[DeckPokemon], energies: Collection[str]
) -> str | None:
    """条件を満たしていなければ、その理由（「〜がいないと働きません」）。満たしていれば `None`。

    `energies` はデッキのエネルギー（和名 1 字）。
    """
    rows = list(pokemon)
    if requirement.types and not any(p.energy_type in requirement.types for p in rows):
        return f"{'・'.join(sorted(requirement.types))}ポケモンがいないと働きません"
    if requirement.energies and not (requirement.energies & set(energies)):
        return f"{'・'.join(sorted(requirement.energies))}エネルギーのデッキでしか働きません"
    if requirement.stages and not any(p.stage in requirement.stages for p in rows):
        stages = "・".join(str(stage) for stage in sorted(requirement.stages))
        return f"{stages} 進化のポケモンがいないと働きません"
    if requirement.lineages and not any(p.lineage in requirement.lineages for p in rows):
        return f"{'・'.join(sorted(requirement.lineages))}のポケモンがいないと働きません"
    if requirement.mega_ex and not any(
        p.name.startswith("Mega ") and p.name.endswith(" ex") for p in rows
    ):
        return "メガシンカ ex がいないと働きません"
    if requirement.pokemon and not (requirement.pokemon & {p.name for p in rows}):
        return f"{'・'.join(sorted(requirement.pokemon))} がいないと働きません"
    return None


def required_type_of(effect: str) -> str | None:
    """効果文が要求する自分のポケモンのタイプ。条件がなければ `None`（`requirement_of` の一部）。"""
    types = requirement_of(effect).types
    return min(types) if types else None


@cache
def _pokemon_names() -> frozenset[str]:
    """実装済みポケモンの名前。効果文が特定のカードを指しているかの判定に使う。"""
    ids = [c.id for c in engine.card_statuses_all() if c.is_complete]
    return frozenset(c.name for c in engine.card_details(ids) if c.kind == "ポケモン")


@cache
def required_pokemon_of(effect: str) -> frozenset[str]:
    """効果文が名指ししている自分のポケモン。指定がなければ空。

    実装済みカードの名前と突き合わせるので、`Item` や `Active` のような
    カード名でない語を拾わない。
    """
    found = {name for name in _pokemon_names() if re.search(rf"\byour {re.escape(name)}\b", effect)}
    if not found:
        return frozenset()
    # 「your Garchomp or Togekiss」のように、2 つ目以降は your が付かない
    for name in _pokemon_names():
        if name not in found and re.search(rf"(?:or|and) {re.escape(name)}\b", effect):
            found.add(name)
    return frozenset(found)
