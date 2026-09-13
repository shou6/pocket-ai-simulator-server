"""利用者が付ける探索の制約：軸にするカード（F-09）と、使わないカード（F-10）。

20 枚・同名 2 枚などのデッキの規則（`recipe.violations`）とは別に持つ。
規則はどのデッキにも掛かるが、こちらは提案 1 回ごとに利用者が決める。

カードは**機能カード単位**で数える。再録・レアリティ違いは同じカードとして扱い、
デッキコードの番号（`deck_builder_ids`）で同一視する。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache

import pocket_engine_py as engine

from pocket_api.cards.deck_builder_ids import deck_builder_ids
from pocket_api.optimize.conditions import JAPANESE_ENERGY
from pocket_api.optimize.recipe import DECK_SIZE, MAX_SAME_NAME, DeckRecipe, violations

MAX_ENERGY_TYPES = 3
"""デッキに指定できるエネルギーの種類の上限。"""

_ENGLISH_ENERGY = {ja: en for en, ja in JAPANESE_ENERGY.items()}
_ENERGY_ORDER = tuple(JAPANESE_ENERGY)


def functional_key(card_id: str) -> str:
    """機能カードの識別子。再録は同じ値になる。対応表に無ければ ID のまま。"""
    key = deck_builder_ids().key_of(card_id)
    return f"{key[0]}:{key[1]}" if key else card_id


@dataclass(frozen=True)
class DeckConstraints:
    """提案 1 回ぶんの制約。"""

    required: tuple[tuple[str, int], ...] = ()
    """（カード ID, 最低枚数）。この枚数以上を必ず入れる。"""

    excluded: frozenset[str] = field(default_factory=frozenset)
    """入れないカード ID。同じ機能カードの再録も入れない。"""

    @classmethod
    def of(
        cls, required: Mapping[str, int] | None = None, excluded: Iterable[str] = ()
    ) -> DeckConstraints:
        return cls(
            required=tuple(sorted((required or {}).items())),
            excluded=frozenset(excluded),
        )

    @property
    def empty(self) -> bool:
        return not self.required and not self.excluded

    def is_excluded(self, card_id: str) -> bool:
        if not self.excluded:
            return False
        return functional_key(card_id) in _excluded_keys(self.excluded)

    def violations(self, recipe: DeckRecipe) -> tuple[str, ...]:
        """満たしていない制約。すべて満たしていれば空。"""
        if self.empty:
            return ()
        from pocket_api.optimize.search import evolution_is_playable

        found: list[str] = []
        by_key: dict[str, int] = {}
        for card_id, count in recipe.cards:
            key = functional_key(card_id)
            by_key[key] = by_key.get(key, 0) + count
            if self.is_excluded(card_id):
                found.append(f"使わないカードが入っています（{_name(card_id)}）")
        for card_id, count in self.required:
            have = by_key.get(functional_key(card_id), 0)
            if have < count:
                found.append(f"軸のカードが足りません（{_name(card_id)} {have} / {count} 枚）")
            elif not evolution_is_playable(recipe, card_id):
                # 入っていても進化元がなければ場に出せず、軸にした意味がない
                found.append(f"軸のカードの進化元がありません（{_name(card_id)}）")
        return tuple(found)


@cache
def _excluded_keys(excluded: frozenset[str]) -> frozenset[str]:
    return frozenset(functional_key(card_id) for card_id in excluded)


@cache
def _details(card_id: str) -> engine.CardDossier | None:
    found = engine.card_details([card_id])
    return found[0] if found else None


def _name(card_id: str) -> str:
    card = _details(card_id)
    return card.name if card else card_id


def axis_energy(required: Sequence[tuple[str, int]], fallback: tuple[str, ...]) -> tuple[str, ...]:
    """軸のカードを動かすのに要るエネルギー。

    軸のポケモンの**打点がいちばん大きいワザ**のコストから、無色を除いたタイプを集める。
    元のデッキ（`fallback`）のエネルギーで足りていればそのまま使う。
    軸がトレーナーズや無色だけのワザのポケモンなら、エネルギーを縛らない。
    """
    wanted: set[str] = set()
    for card_id, _ in required:
        card = _details(card_id)
        if card is None or card.kind != "ポケモン" or not card.attacks:
            continue
        main = max(card.attacks, key=lambda attack: attack.damage)
        wanted |= {kind for kind in main.cost if kind != "無"}
    if not wanted:
        return fallback
    have = {JAPANESE_ENERGY.get(name, name) for name in fallback}
    if wanted <= have:
        return fallback
    if len(wanted) > MAX_ENERGY_TYPES:
        raise ValueError(f"軸のカードに要るエネルギーが {len(wanted)} 種あり、組めません")
    names = [_ENGLISH_ENERGY[kind] for kind in wanted if kind in _ENGLISH_ENERGY]
    return tuple(sorted(names, key=_ENERGY_ORDER.index))


def build_seed(
    base: DeckRecipe,
    constraints: DeckConstraints,
    filler: Sequence[str],
) -> DeckRecipe | None:
    """メタデッキを元に、制約を満たす種のデッキを作る。作れなければ `None`。

    1. 軸のカードと、その進化元（各 2 枚）を入れる
    2. 元のデッキの札を、枚数の多い順に入れる（ポケモン → トレーナーズ）。
       エネルギーが変わって動かなくなった札・使わないカードは飛ばす
    3. 20 枚に足りなければ `filler` の順に 1 枚ずつ足す

    探索（GA と山登り）はこの種から始まるので、ここでは「制約を満たして動く 20 枚」を
    作ることだけを目指し、良し悪しは探索に任せる。
    """
    from pocket_api.optimize.search import evolution_is_playable, type_matches

    energy = axis_energy(constraints.required, base.energy)
    counts: dict[str, int] = {}

    def recipe() -> DeckRecipe:
        return DeckRecipe(cards=tuple(sorted(counts.items())), energy=energy)

    def names() -> dict[str, int]:
        found: dict[str, int] = {}
        for card_id, count in counts.items():
            found[_name(card_id)] = found.get(_name(card_id), 0) + count
        return found

    def try_add(card_id: str, count: int, *, forced: bool = False) -> None:
        for _ in range(count):
            if sum(counts.values()) >= DECK_SIZE:
                return
            if not forced and constraints.is_excluded(card_id):
                return
            if names().get(_name(card_id), 0) >= MAX_SAME_NAME:
                return
            current = recipe()
            if not forced and not type_matches(current, card_id):
                return
            if not forced and not evolution_is_playable(current, card_id):
                return
            candidate = current.with_change(card_id, 1)
            if not forced and violations(candidate, partial=True):
                return
            counts[card_id] = counts.get(card_id, 0) + 1

    # 1. 軸と進化元。進化元から入れる（あとから入れる進化カードが腐らないように）
    for card_id, count in constraints.required:
        for ancestor in reversed(_ancestors(card_id, base, energy)):
            try_add(ancestor, MAX_SAME_NAME, forced=True)
        try_add(card_id, count, forced=True)

    # 2. 元のデッキの札。たね → 進化 → トレーナーズの順に入れる
    def order(item: tuple[str, int]) -> tuple[int, int, int]:
        card = _details(item[0])
        is_pokemon = card is not None and card.kind == "ポケモン"
        stage = (card.stage or 0) if card is not None else 0
        return (0 if is_pokemon else 1, stage, -item[1])

    for card_id, count in sorted(base.cards, key=order):
        try_add(card_id, count)

    # 3. 足りない分
    for card_id in filler:
        if sum(counts.values()) >= DECK_SIZE:
            break
        try_add(card_id, 1)

    built = recipe()
    if built.size != DECK_SIZE or violations(built) or constraints.violations(built):
        return None
    return built


def _ancestors(card_id: str, base: DeckRecipe, energy: tuple[str, ...]) -> list[str]:
    """進化元を近い順に。元のデッキにあればその印刷を、なければ実装済みの先頭を使う。"""
    from pocket_api.optimize.search import implemented_card_ids_named

    in_base = {_name(cid): cid for cid, _ in base.cards}
    chain: list[str] = []
    card = _details(card_id)
    seen: set[str] = set()
    while card is not None and card.evolves_from and card.evolves_from not in seen:
        name = card.evolves_from
        seen.add(name)
        options = implemented_card_ids_named(name)
        picked = in_base.get(name) or (options[0] if options else None)
        if picked is None:
            break
        chain.append(picked)
        card = _details(picked)
    return chain


def filler_order(decklists: Collection[str], pool: Sequence[str]) -> tuple[str, ...]:
    """種のデッキの穴埋めに使う札の順。メタデッキでよく使われている札から。"""
    usage: dict[str, int] = {}
    for text in decklists:
        for card_id, count in DeckRecipe.from_text(text).cards:
            usage[card_id] = usage.get(card_id, 0) + count
    allowed = set(pool)
    popular = sorted((cid for cid in usage if cid in allowed), key=lambda cid: (-usage[cid], cid))
    rest = [cid for cid in pool if cid not in usage]
    return tuple(popular + rest)
