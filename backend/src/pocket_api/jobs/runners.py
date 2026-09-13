"""ジョブの種類ごとの中身。`optimize` の関数を呼び、結果を JSON にする。

ロジックは `optimize` に置き、ここでは条件の受け渡しと表示用の整形だけをする。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from pydantic import BaseModel

from pocket_api.api.schemas import ArchetypeOut, CardOut, DeckOut, Precision
from pocket_api.cards.catalog import CardView, card_view, translator
from pocket_api.db.meta import snapshot_archetypes, snapshot_decks
from pocket_api.optimize.archetype import MetaArchetype, classify
from pocket_api.optimize.evaluate import Evaluation, weighted_interval
from pocket_api.optimize.improve_cli import delta_noise, judge_delta
from pocket_api.optimize.propose import MetaDeck, SearchSettings, propose
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.refine import RefineSettings, refine_deck

STRATEGY = "l"
"""方策は校正済みの `l` だけ（`docs/ui-design.md` 5 節）。"""

IMPROVE_METHOD = "climb"
"""改善のやり方。1 枚入れ替えだけ（旧）の結果と混ぜないよう、条件と結果に入れる。"""

FALLBACK_ESTIMATE_SECONDS: dict[str, dict[int, int]] = {
    "improve": {60: 250, 200: 630, 800: 2000},
    "optimize": {60: 300, 200: 600, 800: 2000},
}
"""所要時間の目安の初期値。**実行した記録があればそちらを使う**（`jobs.estimates`）。

2026-09-13 の実測。デッキ提案（ふつう）は 1 回 565 秒で、そのうち 507 秒が探索中の対戦
（79 デッキ × 相手 10 × 60 試合 ≒ 4.7 万試合を毎秒 95 試合）。時間は探索の規模と
エンジンの速度で決まり、エンジンの速度はマシンの負荷で 3 倍ほど変わる。
"""


def refine_settings(games: int) -> RefineSettings:
    """改善の測りかた（3 択）→ 設定。デッキ提案と同じく、3 択は測り直しの試合数を指す。

    - 速い：最大 3 枚、1 枚ごとに実評価するのは 8 件
    - ふつう：最大 5 枚、1 枚ごとに 20 件（3.47 で当たりに届いた件数）
    - 正確：探索も学習データのある 200 試合に上げる
    """
    if games == 60:
        return RefineSettings(
            strategy=STRATEGY, games=60, report_games=60, max_steps=3, screen_keep=8
        )
    if games == 800:
        return RefineSettings(strategy=STRATEGY, games=200, report_games=800)
    return RefineSettings(strategy=STRATEGY, games=60, report_games=200)


def optimize_settings(games: int) -> SearchSettings:
    """デッキ提案の測りかた（3 択）→ 探索の設定。

    画面の「1 組の試合数」は、結果として見せる**測り直し**の試合数を指す。
    時間のほとんどは探索中の対戦なので、測り直しの試合数を減らしても速くならない。

    - 速い：探索の規模を小さくする（個体 8 × 世代 3 ＋山登り 1 手、1 手で実評価するのは 8 件）。
      山登りは 1 手ごとに `screen_keep` 件を実評価するので、GA を縮めるだけでは速くならなかった
      （個体 8 × 世代 3 ＋山登り 2 手・20 件で 648 秒、既定の 565 秒より遅い）。
      8 件は当たりを取りこぼしやすい（3.48）ので、そのぶん提案の質は下がる
    - ふつう：3.48 で決めた既定（個体 12 × 世代 4 ＋山登り 3 手、探索 60 試合）
    - 正確：探索も学習データのある 200 試合に上げる
    """
    if games == 60:
        return SearchSettings(
            strategy=STRATEGY,
            games=60,
            report_games=60,
            population=8,
            generations=3,
            climb_steps=1,
            screen_keep=8,
        )
    if games == 800:
        return SearchSettings(strategy=STRATEGY, games=200, report_games=800)
    return SearchSettings(strategy=STRATEGY, games=60, report_games=200)


Progress = Callable[[str, str], None]
SessionFactory = Callable[[], AbstractContextManager[Any]]
Runner = Callable[[dict[str, Any], Progress], dict[str, Any]]


class SwapStepOut(BaseModel):
    """改善で入れ替えた 1 枚。`step` は何枚目か。"""

    step: int
    out_card: CardOut
    in_card: CardOut


class OpponentCompare(BaseModel):
    name: str
    name_ja: str
    share: float
    base: float
    final: float


class ImproveResult(BaseModel):
    method: str = IMPROVE_METHOD
    base_deck: DeckOut
    base_archetype: ArchetypeOut | None = None
    final_deck: DeckOut
    final_archetype: ArchetypeOut | None = None
    base_win_rate: float
    base_interval: tuple[float, float]
    win_rate: float
    interval: tuple[float, float]
    delta: float
    verdict: str
    """`clear` / `likely` / `none` / `worse`（`improve_cli.judge_delta`）。"""

    swaps: list[SwapStepOut]
    per_opponent: list[OpponentCompare]
    max_steps: int
    search_games: int
    evaluated: int
    model_note: str
    precision: Precision


class OpponentRate(BaseModel):
    name: str
    name_ja: str
    share: float
    win_rate: float


class ProposalOut(BaseModel):
    rank: int
    archetype: ArchetypeOut | None = None
    win_rate: float
    interval: tuple[float, float]
    per_opponent: list[OpponentRate]
    deck: DeckOut


class OptimizeResult(BaseModel):
    proposals: list[ProposalOut]
    target: str | None
    target_ja: str | None
    axis: list[CardOut]
    searched_win_rate: float
    search_games: int
    climb_steps: int
    pool_size: int
    model_note: str
    precision: Precision


def _card(card_id: str) -> CardOut:
    view = card_view(card_id) or CardView(
        id=card_id,
        name=card_id,
        name_ja=card_id,
        kind="不明",
        energy_type=None,
        stage=None,
        hp=None,
        implemented=False,
    )
    return CardOut.of(view)


def _meta(
    open_session: SessionFactory, snapshot_id: str
) -> tuple[list[MetaDeck], list[MetaArchetype]]:
    """評価の相手と、アーキタイプの定義。"""
    with open_session() as session:
        key = uuid.UUID(snapshot_id)
        decks = [
            MetaDeck(name=d.name, share=d.share, decklist=d.decklists[0])
            for d in snapshot_decks(session, key)
            if d.decklists
        ]
        return decks, snapshot_archetypes(session, key)


def run_improve(
    params: dict[str, Any], progress: Progress, *, open_session: SessionFactory
) -> dict[str, Any]:
    """デッキの改善（画面 ③）。上がらなくなるまで 1 枚ずつ入れ替え、最後に測り直す。"""
    games = int(params["games"])
    settings = refine_settings(games)
    meta, archetypes = _meta(open_session, str(params["snapshot_id"]))
    decklist = str(params["decklist"])
    report = refine_deck(
        decklist,
        [(d.name, d.share, d.decklist) for d in meta],
        settings=settings,
        excluded=set(params.get("excluded", [])),
        progress=progress,
    )
    shares = {d.name: d.share for d in meta}
    names = translator()
    base_rates = dict(report.base_evaluation.per_opponent)
    final_rates = dict(report.final_evaluation.per_opponent)
    delta = report.final_evaluation.win_rate - report.base_evaluation.win_rate
    reported = settings.report_games

    def interval(evaluation: Evaluation) -> tuple[float, float]:
        return weighted_interval(
            [(shares.get(name, 0.0), rate) for name, rate in evaluation.per_opponent],
            evaluation.games,
        )

    result = ImproveResult(
        base_deck=DeckOut.from_recipe(report.base),
        base_archetype=ArchetypeOut.of(classify(report.base, archetypes)),
        final_deck=DeckOut.from_recipe(report.final),
        final_archetype=ArchetypeOut.of(classify(report.final, archetypes)),
        base_win_rate=report.base_evaluation.win_rate,
        base_interval=interval(report.base_evaluation),
        win_rate=report.final_evaluation.win_rate,
        interval=interval(report.final_evaluation),
        delta=delta,
        verdict=judge_delta(delta, reported),
        swaps=[
            SwapStepOut(step=swap.step, out_card=_card(swap.out_card), in_card=_card(swap.in_card))
            for swap in report.swaps
        ],
        per_opponent=[
            OpponentCompare(
                name=name,
                name_ja=names.text(name),
                share=shares.get(name, 0.0),
                base=base_rates[name],
                final=final_rates.get(name, base_rates[name]),
            )
            for name in sorted(base_rates, key=lambda n: -shares.get(n, 0.0))
        ],
        max_steps=settings.max_steps,
        search_games=settings.games,
        evaluated=report.evaluated,
        model_note=report.model_note,
        precision=Precision(strategy=STRATEGY, games=reported, noise=delta_noise(reported)),
    )
    return result.model_dump(mode="json")


def run_optimize(
    params: dict[str, Any], progress: Progress, *, open_session: SessionFactory
) -> dict[str, Any]:
    """デッキ提案（画面 ④。F-03 / F-05 / F-09）。"""
    games = int(params["games"])
    settings = optimize_settings(games)
    meta, archetypes = _meta(open_session, str(params["snapshot_id"]))
    target = params.get("target")
    required = {str(k): int(v) for k, v in dict(params.get("required", {})).items()}
    report = propose(
        meta,
        settings=settings,
        target=target,
        required=required,
        excluded=set(params.get("excluded", [])),
        progress=progress,
    )
    shares = {d.name: d.share for d in meta}
    names = translator()
    result = OptimizeResult(
        proposals=[
            ProposalOut(
                rank=rank,
                archetype=ArchetypeOut.of(classify(recipe, archetypes)),
                win_rate=evaluation.win_rate,
                interval=weighted_interval(
                    [
                        (1.0 if target else shares.get(name, 0.0), rate)
                        for name, rate in evaluation.per_opponent
                    ],
                    evaluation.games,
                ),
                per_opponent=sorted(
                    (
                        OpponentRate(
                            name=name,
                            name_ja=names.text(name),
                            share=shares.get(name, 0.0),
                            win_rate=rate,
                        )
                        for name, rate in evaluation.per_opponent
                    ),
                    key=lambda row: -row.win_rate,
                ),
                deck=DeckOut.from_recipe(recipe),
            )
            for rank, (recipe, evaluation) in enumerate(report.ranked, start=1)
        ],
        target=target,
        target_ja=names.text(target) if target else None,
        axis=[_card(card_id) for card_id in sorted(required)],
        searched_win_rate=report.searched_win_rate,
        search_games=settings.games,
        climb_steps=report.climb_steps,
        pool_size=report.pool_size,
        model_note=report.model_note,
        precision=Precision(
            strategy=STRATEGY,
            games=settings.report_games,
            noise=delta_noise(settings.report_games),
        ),
    )
    return result.model_dump(mode="json")


def default_runners(open_session: SessionFactory) -> dict[str, Runner]:
    """ジョブの種類 → 中身。"""

    def improve(params: dict[str, Any], progress: Progress) -> dict[str, Any]:
        return run_improve(params, progress, open_session=open_session)

    def optimize(params: dict[str, Any], progress: Progress) -> dict[str, Any]:
        return run_optimize(params, progress, open_session=open_session)

    return {"improve": improve, "optimize": optimize}


def canonical_params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """鍵がぶれないように条件を揃える（デッキの書き方・並び順）。"""
    fixed = dict(params)
    if "decklist" in fixed:
        fixed["decklist"] = DeckRecipe.from_text(str(fixed["decklist"])).to_text()
    if "excluded" in fixed:
        fixed["excluded"] = sorted(set(fixed["excluded"]))
    if "required" in fixed:
        fixed["required"] = dict(sorted(dict(fixed["required"]).items()))
    return fixed
