"""デッキ探索。

`docs/requirements.md` 8 章の方針に沿う。

- 初期集団はメタデッキとその派生。ランダム生成だけに頼らない
- 局所探索は 1 枚入れ替え（`neighbours`）
- 制約を満たさない候補は最初から作らない
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache
from typing import TYPE_CHECKING

import pocket_engine_py as engine

from pocket_api.optimize.conditions import (
    DeckPokemon,
    requirement_of,
    unmet,
)
from pocket_api.optimize.recipe import DeckRecipe, Ownership, violations
from pocket_api.optimize.screening import screen
from pocket_api.optimize.similar import similar_cards

if TYPE_CHECKING:
    from pocket_api.optimize.constraints import DeckConstraints

# エネルギータイプの英名から、カード情報で使われている和名への対応
_JAPANESE_ENERGY = {
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


@dataclass(frozen=True)
class CardInfo:
    """探索で使うカードの情報。エンジンが返す詳細から必要な分だけ持つ。"""

    id: str
    name: str
    kind: str
    energy_type: str | None
    stage: int | None
    evolves_from: str | None
    attack_costs: tuple[tuple[str, ...], ...]
    """ワザごとの必要エネルギー。無色は「無」。"""

    has_ability: bool
    """特性を持つか。ワザが撃てなくても、特性が目当てなら入れる価値がある。"""

    effect: str
    """トレーナーズの効果文。タイプ条件の判定に使う。"""

    lineage: str | None = None
    """古代・未来のポケモンなら `古代` / `未来`。"""


@cache
def _implemented_cards() -> tuple[CardInfo, ...]:
    """実装済みカードの詳細。件数が多いので一度だけ読む。"""
    ids = [c.id for c in engine.card_statuses_all() if c.is_complete]
    return tuple(
        CardInfo(
            id=c.id,
            name=c.name,
            kind=c.kind,
            energy_type=c.energy_type,
            stage=c.stage,
            evolves_from=c.evolves_from,
            attack_costs=tuple(tuple(a.cost) for a in c.attacks),
            has_ability=c.ability is not None,
            effect=c.effect or "",
            lineage=c.lineage,
        )
        for c in engine.card_details(ids)
    )


@cache
def _card_by_id() -> dict[str, CardInfo]:
    """カード ID から詳細を引く。"""
    return {c.id: c for c in _implemented_cards()}


@cache
def _evolves_from_of(name: str) -> str | None:
    """そのカード名の進化元。見つからなければ `None`。"""
    for card in _implemented_cards():
        if card.name == name:
            return card.evolves_from
    return None


@cache
def implemented_card_ids_named(name: str) -> tuple[str, ...]:
    """その名前の実装済みカード ID。ID 順。"""
    return tuple(sorted(card.id for card in _implemented_cards() if card.name == name))


def pick_card_pool(decklists: list[str]) -> tuple[str, ...]:
    """探索で使える札。種となるデッキに入っているカードから集める。

    環境で使われているカードだけを候補にすることで、意味のない札を試さずに済む。
    """
    pool: set[str] = set()
    for text in decklists:
        for card_id, _ in DeckRecipe.from_text(text).cards:
            pool.add(card_id)
    return tuple(sorted(pool))


def full_card_pool(
    energy: tuple[str, ...],
    *,
    ownership: Ownership | None = None,
) -> tuple[str, ...]:
    """そのエネルギータイプのデッキで使える、実装済みカードすべて。

    メタデッキの札だけを候補にすると、出てくるのはメタデッキの近傍だけになる。
    「まだ見つかっていないデッキ」を作るには、環境で使われていない札も試す必要がある。

    ポケモンはデッキのタイプか無色のものだけを入れる。他タイプのポケモンは
    エネルギーが供給されず動かない。トレーナーズはタイプを問わない。
    """
    wanted = {_JAPANESE_ENERGY.get(name, name) for name in energy}
    # 先にこのタイプで使えるポケモンを洗い出す。条件付きのトレーナーズの判定に使う
    usable_pokemon = [
        DeckPokemon(
            name=card.name,
            energy_type=card.energy_type,
            stage=card.stage or 0,
            lineage=card.lineage,
        )
        for card in _implemented_cards()
        if card.kind == "ポケモン"
        and (ownership is None or ownership.owned(card.id) > 0)
        and (
            any({k for k in cost if k != "無"} <= wanted for cost in card.attack_costs)
            or card.has_ability
        )
    ]
    pool: list[str] = []
    for card in _implemented_cards():
        if ownership is not None and ownership.owned(card.id) <= 0:
            continue
        if card.kind != "ポケモン":
            # 条件付きのトレーナーズ（タイプ・進化段階・名指しなど）は、条件を満たす
            # ポケモンがこのタイプで使えなければ死に札。ここではプール全体を見るので、
            # 条件を満たすポケモンがプールに残るかで判断する。デッキごとの判定は `violations`
            requirement = requirement_of(card.effect)
            # タイプの条件はエネルギーで見る。無色のワザや特性で他タイプのポケモンも
            # 置けてしまうが、コルニのような札を超デッキに入れる意味はない（ユーザーの指摘）
            if requirement.types and not requirement.types & (wanted | {"無"}):
                continue
            rest = replace(requirement, types=frozenset())
            if not rest.empty and unmet(rest, usable_pokemon, wanted) is not None:
                continue
            pool.append(card.id)
            continue
        usable = any(
            {kind for kind in cost if kind != "無"} <= wanted for cost in card.attack_costs
        )
        if usable or card.has_ability:
            pool.append(card.id)
    return tuple(sorted(pool))


def type_matches(recipe: DeckRecipe, card_id: str) -> bool:
    """そのカードをこのデッキに入れて動くか。

    見るのはカードのタイプではなく **ワザのコスト**。無色だけのワザはどのデッキでも撃てるし、
    タイプの違うポケモンでも無色コストなら戦力になる（超デッキのダークライは無無無、
    草デッキのケロマツは無 1 個）。

    ワザが撃てなくても、特性が目当ての採用はある（草デッキのゲッコウガ）。
    そういう札も残す。ユーザーの指摘で、カードのタイプで弾いていた誤りを直した。
    """
    info = _card_by_id().get(card_id)
    if info is None or info.kind != "ポケモン":
        return True
    wanted = {_JAPANESE_ENERGY.get(name, name) for name in recipe.energy}
    for cost in info.attack_costs:
        needed = {kind for kind in cost if kind != "無"}
        if needed <= wanted:
            return True
    # 撃てなくても、特性のために置くことがある
    return info.has_ability


def evolution_is_playable(recipe: DeckRecipe, card_id: str) -> bool:
    """その進化カードを入れても腐らないか。

    進化元がデッキにあるか、同じ系統のたねがあれば出せる
    （ふしぎなアメで 2 進化に飛ぶ構成もあるので、たねまで遡って見る）。
    """
    info = _card_by_id().get(card_id)
    if info is None or info.kind != "ポケモン" or not info.stage:
        return True
    names = {_card_by_id()[cid].name for cid, _ in recipe.cards if cid in _card_by_id()}
    ancestor = info.evolves_from
    while ancestor:
        if ancestor in names:
            return True
        ancestor = _evolves_from_of(ancestor)
    return False


def neighbours(
    recipe: DeckRecipe,
    pool: tuple[str, ...],
    *,
    ownership: Ownership | None = None,
    limit: int | None = None,
    rng: random.Random | None = None,
    similar_width: int | None = None,
    constraints: DeckConstraints | None = None,
) -> tuple[DeckRecipe, ...]:
    """1 枚入れ替えでできる、制約を満たすデッキ。

    `constraints`（軸のカード・使わないカード）を渡すと、それを破る入れ替えも作らない。

    枚数は 20 枚のまま保つので、抜く 1 枚と入れる 1 枚の組を試す。
    候補は「デッキの種類数 × プールの大きさ」まで膨らむ。評価が重いときは
    `limit` で抜き出す数を決められる（`rng` を渡せば同じ顔ぶれになる）。
    """
    found: list[DeckRecipe] = []
    seen: set[tuple[tuple[str, int], ...]] = {recipe.cards}
    for out_id, _ in recipe.cards:
        removed = recipe.with_change(out_id, -1)
        # 札が数百を超えると総当たりは重い。役割の近いものだけに絞る
        candidates = (
            tuple(cid for cid, _ in similar_cards(out_id, limit=similar_width, pool=pool))
            if similar_width is not None
            else pool
        )
        for in_id in candidates:
            if in_id == out_id or not type_matches(recipe, in_id):
                continue
            candidate = removed.with_change(in_id, 1)
            if candidate.cards in seen:
                continue
            if violations(candidate, ownership=ownership):
                continue
            if not evolution_is_playable(candidate, in_id):
                continue
            if constraints is not None and constraints.violations(candidate):
                continue
            seen.add(candidate.cards)
            found.append(candidate)
    if limit is not None and len(found) > limit:
        source = rng if rng is not None else random.Random(1)
        return tuple(source.sample(found, limit))
    return tuple(found)


@dataclass(frozen=True)
class ClimbResult:
    """山登りの結果。"""

    recipe: DeckRecipe
    """到達したデッキ。"""

    score: float
    """その評価値。"""

    steps: int
    """実際に動いた回数。"""


def hill_climb(
    start: DeckRecipe,
    pool: tuple[str, ...],
    score: Callable[[DeckRecipe, int], float],
    *,
    ownership: Ownership | None = None,
    max_steps: int = 10,
    limit: int | None = None,
    rng: random.Random | None = None,
    similar_width: int | None = None,
    predict: Callable[[DeckRecipe], float] | None = None,
    screen_keep: int = 8,
    constraints: DeckConstraints | None = None,
    on_move: Callable[[int, DeckRecipe, DeckRecipe, float], None] | None = None,
) -> ClimbResult:
    """1 枚入れ替えを繰り返して、評価が上がらなくなるまで登る。

    `on_move` を渡すと、動くたびに（手番, 動く前, 動いた後, 評価）で呼ぶ。道筋を見せるのに使う。

    評価は呼び出し側から渡す（`evaluate.expected_win_rate` を想定）。第 2 引数は
    手番の番号で、**手番ごとに違う試合で測る**ために使う（下記）。

    `predict` を渡すと、近傍をモデルの予測で絞ってから上位だけ実評価する。
    評価 1 回が重いので、この絞り込みで見られる候補が桁違いに増える。

    `limit` は**モデルが無いときだけ**効く。モデルが無ければ候補の数だけ実評価が
    掛かるので歯止めが要るが、モデルがあれば実評価に回るのは `screen_keep` 件だけ。
    そこで間引いても実評価の回数は変わらず、当たりを捨てるだけになる
    （`docs/analysis/card-pool-expansion.md`）。

    **いま登っているデッキの点数は測り直さない。** その点数は多数の候補のうち最も
    高く出たものなので運で高く出ており、そのぶん基準が高くなって 1 手で止まりやすい。
    手番ごとに測り直す形も試したが、**実測では悪くなった**（3 シード平均 60.4% →
    59.3%、時間 1.4 倍）。水増しした基準が「雑音で良く見えただけの候補」への移動を
    止める歯止めになっていたため。詳しくは `docs/analysis/card-pool-expansion.md`。
    """
    current = start
    current_score = score(current, 0)
    for step in range(max_steps):
        best: DeckRecipe | None = None
        best_score = current_score
        found = neighbours(
            current,
            pool,
            ownership=ownership,
            limit=None if predict is not None else limit,
            rng=rng,
            similar_width=similar_width,
            constraints=constraints,
        )

        def at_step(recipe: DeckRecipe, _step: int = step) -> float:
            return score(recipe, _step)

        # モデルがあれば予測で絞り、上位だけ実評価する。予測だけで決めると外す
        pairs = (
            screen(found, predict, at_step, keep=screen_keep)
            if predict is not None
            else tuple((candidate, at_step(candidate)) for candidate in found)
        )
        for candidate, value in pairs:
            if value > best_score:
                best, best_score = candidate, value
        if best is None:
            return ClimbResult(recipe=current, score=best_score, steps=step)
        if on_move is not None:
            on_move(step, current, best, best_score)
        current, current_score = best, best_score
    return ClimbResult(recipe=current, score=current_score, steps=max_steps)
