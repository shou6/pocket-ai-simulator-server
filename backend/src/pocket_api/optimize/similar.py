"""役割の似たカードを探す。

用途は 2 つある。

- 探索：1,000 種からランダムに 1 枚選んでも意味のある札は引けない。
  抜くカードと役割が近いものに絞れば、試行が無駄にならない
- サジェスト：提案されたデッキに持っていないカードがあるとき、代わりを示す

ポケモンは（タイプ・進化段階・HP・最大打点・最小コスト）で近さを測る。
トレーナーズは効果文の重なりで測る。どちらも `card_details` から作れるので、
専用のテーブルは要らない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

import pocket_engine_py as engine

# 役割の近さを測るときの重み。HP と打点は 10 ごと、コストは 1 個で 3 点ぶんの差とみなす
HP_WEIGHT = 0.1
DAMAGE_WEIGHT = 0.1
COST_WEIGHT = 3.0


@dataclass(frozen=True)
class CardProfile:
    """カードの役割。近さを測るために必要な分だけ持つ。"""

    id: str
    name: str
    kind: str
    energy_type: str | None
    stage: int | None
    hp: int
    max_damage: int
    min_cost: int
    words: frozenset[str]
    """効果文に出てくる語。トレーナーズの比較に使う。"""


def _words_of(text: str | None) -> frozenset[str]:
    """効果文を語に分ける。英数字の並びを拾い、短すぎる語は捨てる。"""
    if not text:
        return frozenset()
    return frozenset(w for w in re.findall(r"[A-Za-z']+", text.lower()) if len(w) > 2)


@cache
def _profiles() -> tuple[CardProfile, ...]:
    """実装済みカードの役割。件数が多いので一度だけ作る。"""
    ids = [c.id for c in engine.card_statuses_all() if c.is_complete]
    built: list[CardProfile] = []
    for card in engine.card_details(ids):
        text = card.effect or ""
        for attack in card.attacks:
            text += " " + (attack.effect or "")
        if card.ability is not None:
            text += " " + card.ability.effect
        built.append(
            CardProfile(
                id=card.id,
                name=card.name,
                kind=card.kind,
                energy_type=card.energy_type,
                stage=card.stage,
                hp=card.hp or 0,
                max_damage=max((a.damage for a in card.attacks), default=0),
                min_cost=min((len(a.cost) for a in card.attacks), default=9),
                words=_words_of(text),
            )
        )
    return tuple(built)


@cache
def _by_id() -> dict[str, CardProfile]:
    return {p.id: p for p in _profiles()}


def card_profile(card_id: str) -> CardProfile | None:
    """そのカードの役割。実装されていなければ `None`。"""
    return _by_id().get(card_id)


def _distance(base: CardProfile, other: CardProfile) -> float | None:
    """役割の遠さ。比べる意味がない組は `None`。"""
    if base.kind != other.kind:
        return None
    if base.kind == "ポケモン":
        # タイプが違うとエネルギーが供給されない。無色はどのデッキでも動く
        if other.energy_type not in (base.energy_type, "無"):
            return None
        if other.stage != base.stage:
            return None
        return (
            abs(other.hp - base.hp) * HP_WEIGHT
            + abs(other.max_damage - base.max_damage) * DAMAGE_WEIGHT
            + abs(other.min_cost - base.min_cost) * COST_WEIGHT
        )
    # トレーナーズは効果文の重なり（Jaccard 距離）で見る
    if not base.words and not other.words:
        return 1.0
    union = base.words | other.words
    if not union:
        return 1.0
    return 1.0 - len(base.words & other.words) / len(union)


def similar_cards(
    card_id: str,
    *,
    limit: int = 5,
    pool: tuple[str, ...] | None = None,
) -> tuple[tuple[str, str], ...]:
    """役割の近い順に（カード ID, カード名）を返す。自分自身と同名は含めない。

    `pool` を渡すと、その範囲から探す（所持カードに絞るときに使う）。
    """
    base = card_profile(card_id)
    if base is None:
        return ()
    allowed = set(pool) if pool is not None else None
    scored: list[tuple[float, str, str]] = []
    seen_names = {base.name}
    for other in _profiles():
        if other.id == card_id or other.name in seen_names:
            continue
        if allowed is not None and other.id not in allowed:
            continue
        distance = _distance(base, other)
        if distance is None:
            continue
        scored.append((distance, other.id, other.name))
    scored.sort(key=lambda x: (x[0], x[1]))

    picked: list[tuple[str, str]] = []
    for _, other_id, name in scored:
        if name in seen_names:
            continue
        seen_names.add(name)
        picked.append((other_id, name))
        if len(picked) >= limit:
            break
    return tuple(picked)
