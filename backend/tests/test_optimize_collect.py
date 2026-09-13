"""学習データに使うデッキの組み立て。"""

from __future__ import annotations

import random

from pocket_api.optimize.collect import plan_decks
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.similar import similar_cards

BASE = DeckRecipe.from_text(
    """\
Energy: Fire
2 A1 042
2 A1 043
2 A1 044
2 A1 049
2 A1 050
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
"""
)


def _pool() -> tuple[str, ...]:
    from pocket_api.optimize.search import full_card_pool

    return full_card_pool(BASE.energy)


def test_plan_decks_returns_requested_count() -> None:
    """頼んだ数だけデッキを返す。"""
    planned = plan_decks(BASE, _pool(), 12, rng=random.Random(1))
    assert len(planned) == 12


def test_plan_decks_includes_one_card_pairs() -> None:
    """1 枚しか違わない組が含まれる。

    山登りが並べ替えるのは 1 枚違いどうしなので、その組がデータにないと
    モデルは近い候補を見分けられない。
    """
    planned = plan_decks(BASE, _pool(), 12, rng=random.Random(1))
    pairs = sum(1 for i, a in enumerate(planned) for b in planned[i + 1 :] if _distance(a, b) == 1)
    assert pairs > 0


def test_plan_decks_only_swaps_in_role_similar_cards() -> None:
    """入れ替える札は、抜く札と役割が近いものに限る。

    探索は抜く札と役割の近い上位 `similar_width` 件しか候補にしない。
    学習データがプール全体からの無作為な差し替えでできていると、モデルは
    「別物どうし」しか教わらず、本番で並べる候補を見分けられない
    （`docs/analysis/card-pool-expansion.md`）。

    幅 1 なら「各札の一番近い札」だけをたどれるので、出てくる札の顔ぶれを
    先に数え上げて突き合わせられる。
    """
    pool = _pool()
    reachable = {card_id for card_id, _ in BASE.cards}
    frontier = set(reachable)
    while frontier:
        nxt: set[str] = set()
        for card_id in frontier:
            for near_id, _ in similar_cards(card_id, limit=1, pool=pool):
                if near_id not in reachable:
                    reachable.add(near_id)
                    nxt.add(near_id)
        frontier = nxt

    planned = plan_decks(BASE, pool, 30, rng=random.Random(1), similar_width=1)
    used = {card_id for deck in planned for card_id, _ in deck.cards}
    assert used <= reachable, f"役割の近さでたどれない札が入っている: {sorted(used - reachable)}"


def test_plan_decks_is_deterministic() -> None:
    """同じシードなら同じ顔ぶれになる。"""
    first = plan_decks(BASE, _pool(), 8, rng=random.Random(3))
    second = plan_decks(BASE, _pool(), 8, rng=random.Random(3))
    assert [d.to_text() for d in first] == [d.to_text() for d in second]


def test_plan_decks_keeps_twenty_cards() -> None:
    """どのデッキも 20 枚のまま。"""
    planned = plan_decks(BASE, _pool(), 8, rng=random.Random(5))
    assert all(sum(n for _, n in deck.cards) == 20 for deck in planned)


def _distance(a: DeckRecipe, b: DeckRecipe) -> int:
    """2 つのデッキで違う枚数（入れ替え 1 回なら 1）。"""
    left, right = dict(a.cards), dict(b.cards)
    diff = sum(abs(left.get(cid, 0) - right.get(cid, 0)) for cid in left | right)
    return diff // 2
