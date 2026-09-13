"""デッキの特徴量。

サロゲートモデル（勝率を予測する代理評価器）に渡すため、デッキを数値の並びにする。
1 デッキの実評価に約 7 秒かかるのに対し、モデルなら一瞬で済む。探索では数千の候補を
見るので、この差が効く。

特徴量はカードの性能から機械的に作る。「どのカードが入っているか」ではなく
「どういう構成か」を表すので、見たことのないカードを含むデッキにも使える。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import pocket_engine_py as engine

from pocket_api.optimize.recipe import DeckRecipe

FEATURE_NAMES: tuple[str, ...] = (
    "pokemon_count",
    "trainer_count",
    "basic_count",
    "stage1_count",
    "stage2_count",
    "ex_count",
    "ability_count",
    "hp_mean",
    "hp_max",
    "damage_mean",
    "damage_max",
    "cost_mean",
    "cost_min",
    "retreat_mean",
    "draw_count",
    "search_count",
    "tool_count",
    "stadium_count",
    "supporter_count",
    "item_count",
    "energy_types",
    "evolution_lines",
    "evolution_supply",
    "orphan_evolutions",
    "candy_count",
    "main_line_depth",
    "heal_count",
    "boost_count",
    "switch_count",
    "energy_accel_count",
    "disrupt_count",
)
"""特徴量の名前。並びは `deck_features` の戻り値と対応する。"""

_DRAW_CARDS = frozenset({"Professor's Research", "Copycat", "Mars", "Sightseer"})
_SEARCH_CARDS = frozenset({"Poké Ball", "Korrina"})
_CANDY_CARDS = frozenset({"Rare Candy"})


# トレーナーズの役割を効果文から見分ける。「サポート 1 枚」としか見ないと、
# 回復と打点強化が同じデッキに見えてしまう（ユーザーの指摘）
_ROLE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("heal", ("heal ", "recovers from all special conditions")),
    ("boost", ("do +", "does +", "more damage to your opponent")),
    ("switch", ("retreat cost", "switch out your", "switch in")),
    ("accel", ("attach a", "attach 1", "attach an", "take a", "energy from your energy zone")),
    ("disrupt", ("your opponent's hand", "your opponent reveals", "discard a random", "can't")),
)


@dataclass(frozen=True)
class _CardFacts:
    """特徴量を作るのに要るカードの情報。"""

    name: str
    kind: str
    stage: int | None
    hp: int
    max_damage: int
    min_cost: int
    retreat_cost: int
    has_ability: bool
    evolves_from: str | None
    roles: frozenset[str]
    """効果文から読み取った役割（heal / boost / switch / accel / disrupt）。"""


@cache
def _facts() -> dict[str, _CardFacts]:
    """カード ID から情報を引く。件数が多いので一度だけ読む。"""
    ids = [c.id for c in engine.card_statuses_all() if c.is_complete]
    built: dict[str, _CardFacts] = {}
    for card in engine.card_details(ids):
        built[card.id] = _CardFacts(
            name=card.name,
            kind=card.kind,
            stage=card.stage,
            hp=card.hp or 0,
            max_damage=max((a.damage for a in card.attacks), default=0),
            min_cost=min((len(a.cost) for a in card.attacks), default=0),
            retreat_cost=card.retreat_cost or 0,
            has_ability=card.ability is not None,
            evolves_from=card.evolves_from,
            roles=_roles_of(card.effect),
        )
    return built


def _roles_of(effect: str | None) -> frozenset[str]:
    """効果文から役割を読み取る。当てはまらなければ空。"""
    if not effect:
        return frozenset()
    text = effect.lower()
    return frozenset(role for role, words in _ROLE_PATTERNS if any(w in text for w in words))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def deck_features(decklist: str) -> tuple[float, ...]:
    """デッキを特徴量の並びにする。`FEATURE_NAMES` と同じ順。"""
    recipe = DeckRecipe.from_text(decklist)
    facts = _facts()

    pokemon = trainer = basics = stage1 = stage2 = ex = abilities = 0
    draw = search = tool = stadium = supporter = item = candy = 0
    roles: dict[str, int] = {}
    hps: list[float] = []
    damages: list[float] = []
    costs: list[float] = []
    retreats: list[float] = []
    evolves_from_names: set[str] = set()

    for card_id, count in recipe.cards:
        info = facts.get(card_id)
        if info is None:
            continue
        if info.kind == "ポケモン":
            pokemon += count
            if info.stage == 0:
                basics += count
            elif info.stage == 1:
                stage1 += count
            elif info.stage == 2:
                stage2 += count
            if info.name.endswith(" ex") or info.name.startswith("Mega "):
                ex += count
            if info.has_ability:
                abilities += count
            hps += [float(info.hp)] * count
            damages += [float(info.max_damage)] * count
            costs += [float(info.min_cost)] * count
            retreats += [float(info.retreat_cost)] * count
            if info.evolves_from:
                evolves_from_names.add(info.evolves_from)
        else:
            trainer += count
            if info.name in _DRAW_CARDS:
                draw += count
            if info.name in _SEARCH_CARDS:
                search += count
            if info.kind == "ポケモンのどうぐ":
                tool += count
            elif info.kind == "スタジアム":
                stadium += count
            elif info.kind == "サポート":
                supporter += count
            elif info.kind == "グッズ":
                item += count
            if info.name in _CANDY_CARDS:
                candy += count
            for role in info.roles:
                roles[role] = roles.get(role, 0) + count

    supply, orphans, depth = _evolution_health(recipe, facts)
    return (
        float(pokemon),
        float(trainer),
        float(basics),
        float(stage1),
        float(stage2),
        float(ex),
        float(abilities),
        _mean(hps),
        max(hps, default=0.0),
        _mean(damages),
        max(damages, default=0.0),
        _mean(costs),
        min(costs, default=0.0),
        _mean(retreats),
        float(draw),
        float(search),
        float(tool),
        float(stadium),
        float(supporter),
        float(item),
        float(len(recipe.energy)),
        float(len(evolves_from_names)),
        supply,
        orphans,
        float(candy),
        depth,
        float(roles.get("heal", 0)),
        float(roles.get("boost", 0)),
        float(roles.get("switch", 0)),
        float(roles.get("accel", 0)),
        float(roles.get("disrupt", 0)),
    )


def _evolution_health(
    recipe: DeckRecipe,
    facts: dict[str, _CardFacts],
) -> tuple[float, float, float]:
    """進化ラインが成立しているか。（充足の度合い, 腐る進化カードの枚数, 主役系統の厚み）。

    進化元の枚数が足りないと、進化カードは場に出せず腐る。たね同士の入れ替えでも
    ここが変わるので、カード 1 枚の違いを捉えられる（ユーザーの指摘：メガルカリオ
    デッキのリオル 2 枚は、進化先 3 枚ぶんの進化元を担っている）。

    ふしぎなアメは 2 進化を飛ばせるが、たねが要る点は変わらないので充足には数えない。
    """
    counts_by_name: dict[str, int] = {}
    for card_id, count in recipe.cards:
        info = facts.get(card_id)
        if info is None or info.kind != "ポケモン":
            continue
        counts_by_name[info.name] = counts_by_name.get(info.name, 0) + count

    needed: dict[str, int] = {}
    orphans = 0
    for card_id, count in recipe.cards:
        info = facts.get(card_id)
        if info is None or info.kind != "ポケモン" or not info.evolves_from:
            continue
        needed[info.evolves_from] = needed.get(info.evolves_from, 0) + count
        if counts_by_name.get(info.evolves_from, 0) == 0:
            orphans += count

    if not needed:
        # 進化を使わないデッキは、供給の問題がないので満点にする
        return 1.0, 0.0, 0.0
    ratios = [min(1.0, counts_by_name.get(name, 0) / count) for name, count in needed.items()]
    supply = sum(ratios) / len(ratios)

    # 主役系統の厚み：最も打点の高いポケモンと、その進化元の合計枚数
    best_id = max(
        (cid for cid, _ in recipe.cards if facts.get(cid) and facts[cid].kind == "ポケモン"),
        key=lambda cid: facts[cid].max_damage,
        default=None,
    )
    depth = 0.0
    if best_id is not None:
        name: str | None = facts[best_id].name
        while name:
            depth += counts_by_name.get(name, 0)
            info = next((f for f in facts.values() if f.name == name), None)
            name = info.evolves_from if info else None
    return supply, float(orphans), depth
