"""持っていないカードの代わり（F-10）。

役割の近いカード（`similar.similar_cards`）のうち、**このデッキに入れて働くもの**だけを返す。
以前は役割の近さだけで選んでいたので、悪デッキのヘビーメットの代わりにメタルコアバリア（鋼）や
ウォーターボート（水）が並んでいた（2026-09-13 のユーザーの指摘）。

絞り込みは改善の探索（`search.neighbours`）と同じ基準にする。
"""

from __future__ import annotations

from collections.abc import Collection

from pocket_api.optimize.constraints import DeckConstraints
from pocket_api.optimize.recipe import DeckRecipe, violations
from pocket_api.optimize.search import evolution_is_playable, full_card_pool, type_matches
from pocket_api.optimize.similar import similar_cards

SEARCH_WIDTH = 60
"""役割の近い順に何枚まで見るか。デッキに合わない札を飛ばすので、返す数より多めに見る。"""


def alternatives(
    recipe: DeckRecipe,
    card_id: str,
    *,
    limit: int = 6,
    excluded: Collection[str] = (),
) -> tuple[str, ...]:
    """`card_id` の代わりに入れられるカード ID。役割の近い順。

    - デッキのエネルギーで動く（ポケモンはワザのコストか特性、トレーナーズは条件）
    - 差し替えたデッキが、元のデッキに無かった制約違反を生まない（同名 2 枚まで・働かない札など）
    - 進化カードなら進化元がデッキにある
    - 使わないカード（`excluded`）とその再録は入れない
    """
    if recipe.count_of(card_id) == 0:
        raise ValueError(f"デッキに入っていないカードです: {card_id}")
    constraints = DeckConstraints.of(excluded=excluded)
    pool = tuple(
        other for other in full_card_pool(recipe.energy) if not constraints.is_excluded(other)
    )
    before = set(violations(recipe))
    removed = recipe.with_change(card_id, -1)
    picked: list[str] = []
    for other_id, _name in similar_cards(card_id, limit=SEARCH_WIDTH, pool=pool):
        if not type_matches(recipe, other_id):
            continue
        candidate = removed.with_change(other_id, 1)
        if set(violations(candidate)) - before:
            continue
        if not evolution_is_playable(candidate, other_id):
            continue
        picked.append(other_id)
        if len(picked) >= limit:
            break
    return tuple(picked)
