"""デッキ提案（F-03 / F-05 / F-09）。

GA で構成を組み替え、山登りで 1 枚ずつ詰め、最後に**探索とは別の試合で測り直す**。
CLI（`optimize.cli`）とジョブ（`jobs.runners`）の両方から呼ぶ。
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pocket_api.optimize.cache import MatchupCache
from pocket_api.optimize.constraints import DeckConstraints, axis_energy, build_seed, filler_order
from pocket_api.optimize.dataset import Sample, append_samples, load_samples
from pocket_api.optimize.evaluate import Evaluation, Opponent, expected_win_rate
from pocket_api.optimize.features import deck_features
from pocket_api.optimize.genetic import evolve
from pocket_api.optimize.progress import WorkMeter
from pocket_api.optimize.recipe import DeckRecipe, Ownership, violations
from pocket_api.optimize.search import full_card_pool, hill_climb, pick_card_pool
from pocket_api.optimize.surrogate import BoostedSurrogate

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_STORE = REPO_ROOT / "data" / "surrogate" / "samples.jsonl"

MIN_SAMPLES_FOR_MODEL = 100
"""モデルを使うのに要る学習データの数。これ未満では過学習して実評価より当てにならない。"""

INDEPENDENT_SEED_OFFSET = 100_000
"""探索が使ったシードとの距離。同じ試合をなぞらないだけ離れていればよい。"""

STEP_SEED_STRIDE = 1_000
"""山登りの手番ごとにシードをずらす幅。いまは手番 0 しか使わないが、
手番ごとに違う試合で測る形を試せるように残してある。"""

Progress = Callable[[str, str], None]
"""進み具合の報告先。（段階, 説明）。段階は `prepare` / `evaluate` / `finalize`。"""


def _ignore(_stage: str, _detail: str) -> None:
    return None


@dataclass(frozen=True)
class MetaDeck:
    """メタ環境の 1 アーキタイプ。"""

    name: str
    share: float
    decklist: str


def build_opponents(decks: Sequence[MetaDeck], target: str | None = None) -> list[Opponent]:
    """評価に使う相手。`target` を指定するとその 1 デッキだけにする。"""
    if target is None:
        return [Opponent(name=d.name, decklist=d.decklist, share=d.share) for d in decks]
    for deck in decks:
        if deck.name == target:
            return [Opponent(name=deck.name, decklist=deck.decklist, share=1.0)]
    raise ValueError(f"指定のデッキが見つかりません: {target}")


def independent_evaluation(
    recipes: Sequence[DeckRecipe],
    opponents: list[Opponent],
    *,
    strategy: str,
    games: int,
    search_seed: int,
    cache: MatchupCache | None = None,
    rate: Callable[..., Evaluation] = expected_win_rate,
) -> list[tuple[DeckRecipe, Evaluation]]:
    """探索とは別の試合で測り直し、その成績の高い順に返す。

    探索は何百もの候補から「その試合数で最も高く出たもの」を選ぶので、**運で高く
    出たデッキが選ばれる**。探索が出した値をそのまま成績として報告すると、実力より
    高く出る（実測では 60 試合の報告値が 200 試合の実力より 4〜9 点高かった。
    `docs/analysis/card-pool-expansion.md`）。

    同じシード・同じ試合数だと勝率キャッシュが探索時の値をそのまま返すので、
    シードをずらして独立した試合にする。
    """
    scored = [
        (
            recipe,
            rate(
                recipe.to_text(),
                opponents,
                strategy=strategy,
                games=games,
                cache=cache,
                seed=search_seed + INDEPENDENT_SEED_OFFSET,
            ),
        )
        for recipe in recipes
    ]
    scored.sort(key=lambda pair: pair[1].win_rate, reverse=True)
    return scored


@dataclass(frozen=True)
class SearchSettings:
    """探索の設定。既定値は実測で決めたもの（`docs/status.md` 3.28・3.48）。"""

    strategy: str = "l"
    games: int = 60
    """探索中の 1 組あたりの試合数。"""

    report_games: int = 200
    """成績を測り直すときの試合数。"""

    population: int = 12
    generations: int = 4
    climb_steps: int = 3
    neighbours: int = 40
    """山登りで一度に見る候補の数。モデルが無いときだけ効く。"""

    similar_width: int = 16
    screen_keep: int = 20
    pool: str = "all"
    """`meta` はメタデッキの札だけ、`all` は同じタイプの実装済みカード全部。"""

    top: int = 3
    seed: int = 1
    store: Path = DEFAULT_STORE
    use_surrogate: bool = True
    collect_samples: bool = True
    """実評価した結果を学習データとして貯めるか。"""


@dataclass(frozen=True)
class ProposalReport:
    """デッキ提案の結果。"""

    ranked: tuple[tuple[DeckRecipe, Evaluation], ...]
    """測り直した成績の高い順。"""

    searched_win_rate: float
    """探索中の値（実力より高く出る。表示用ではなく記録用）。"""

    climb_steps: int
    pool_size: int
    evaluated_matchups: int
    cache_hits: int
    elapsed_seconds: float
    model_note: str
    samples_written: int = 0


def _load_surrogate(settings: SearchSettings) -> tuple[BoostedSurrogate | None, str]:
    if not settings.use_surrogate:
        return None, "モデルを使わない指定"
    # 方策が違うと物差しが違う（p はバタフリーを 64.6%、l は 54.4% と見積もる）。
    # 試合数もぶれ方が変わるので、探索と同じ条件で集めたデータだけを使う
    samples = load_samples(settings.store, strategy=settings.strategy, games=settings.games)
    if len(samples) < MIN_SAMPLES_FOR_MODEL:
        return None, (
            f"条件の合う学習データが {len(samples)} 件しかないので使いません"
            f"（方策 {settings.strategy} / {settings.games} 試合 / "
            f"{MIN_SAMPLES_FOR_MODEL} 件以上要る）"
        )
    model = BoostedSurrogate.fit([(s.features, s.win_rate) for s in samples])
    return model, f"学習データ {model.samples} 件 / 学習時の誤差 {model.train_error:.3f}"


def propose(
    decks: Sequence[MetaDeck],
    *,
    settings: SearchSettings | None = None,
    target: str | None = None,
    ownership: Ownership | None = None,
    required: Mapping[str, int] | None = None,
    excluded: Collection[str] = (),
    progress: Progress = _ignore,
    meter: WorkMeter | None = None,
) -> ProposalReport:
    """メタ環境（または `target` の 1 デッキ）に強いデッキを探す。

    - `required`：軸にするカード（カード ID → 最低枚数）。全候補に必ず含める（F-09）
    - `excluded`：使わないカード（「持っていない」と言われたもの。F-10）
    """
    if settings is None:
        settings = SearchSettings()
    if not decks:
        raise ValueError("メタのスナップショットにデッキがありません")
    progress("prepare", "種のデッキを用意しています")
    opponents = build_opponents(decks, target)
    constraints = DeckConstraints.of(required, excluded)

    seeds = [DeckRecipe.from_text(d.decklist) for d in decks]
    if ownership is not None:
        seeds = [s for s in seeds if not violations(s, ownership=ownership)]
        if not seeds:
            raise ValueError("所持カードで組めるメタデッキがありません")

    if settings.pool == "meta":
        # メタデッキの札だけ。出てくるのはメタデッキの近傍に限られる
        pool = pick_card_pool([d.decklist for d in decks])
    else:
        # 種のデッキと同じタイプの札を全部使う。まだ知られていない構成を狙う。
        # 軸があれば、軸を動かすエネルギーのデッキになるのでそのタイプも含める
        energies = {e for seed in seeds for e in axis_energy(constraints.required, seed.energy)}
        pool = full_card_pool(tuple(sorted(energies)), ownership=ownership)
    if not constraints.empty:
        pool = tuple(card_id for card_id in pool if not constraints.is_excluded(card_id))
        # メタデッキを軸と使わないカードに合わせて組み直す
        filler = filler_order([d.decklist for d in decks], pool)
        rebuilt = [build_seed(seed, constraints, filler) for seed in seeds]
        unique = {recipe.cards: recipe for recipe in rebuilt if recipe is not None}
        seeds = list(unique.values())
        if not seeds:
            raise ValueError("指定のカードで組める種のデッキを作れませんでした")

    cache = MatchupCache()
    work = meter if meter is not None else WorkMeter()
    collected: list[Sample] = []
    scoreboard: dict[tuple[tuple[str, int], ...], tuple[DeckRecipe, float]] = {}
    phase = ["構成を組み替えています"]

    def score(recipe: DeckRecipe, step: int = 0) -> float:
        text = recipe.to_text()
        rate = expected_win_rate(
            text,
            opponents,
            strategy=settings.strategy,
            games=settings.games,
            cache=cache,
            seed=settings.seed + step * STEP_SEED_STRIDE,
        ).win_rate
        if target is None and constraints.empty:
            # メタ全体への期待勝率のときだけ貯める。特定の相手だけを見た値や、
            # 制約で偏った候補は学習データの分布を変えるので混ぜない
            collected.append(
                Sample.of(text, rate, strategy=settings.strategy, games=settings.games)
            )
        work.advance()
        if recipe.cards not in scoreboard:
            scoreboard[recipe.cards] = (recipe, rate)
            progress("evaluate", f"{phase[0]}（{len(scoreboard)} 通りを評価）")
        return rate

    model, model_note = _load_surrogate(settings)
    predict: Callable[[DeckRecipe], float] | None = None
    if model is not None:
        trained = model

        def predict_with_model(recipe: DeckRecipe) -> float:
            return trained.predict(deck_features(recipe.to_text()))

        predict = predict_with_model

    # 仕事の量の見込み（上限）。GA は世代ごとに集団ぶん、
    # 山登りは 1 手ごとに screen_keep（モデルが無ければ neighbours）件
    ga_work = settings.population * (settings.generations + 1)
    per_step = settings.screen_keep if predict is not None else settings.neighbours
    climb_work = 1 + settings.climb_steps * per_step
    recheck_work = settings.top * math.ceil(settings.report_games / settings.games)
    work.begin(ga_work + climb_work + recheck_work)

    started = time.perf_counter()
    evolved = evolve(
        seeds,
        pool,
        score,
        population=settings.population,
        generations=settings.generations,
        rng=random.Random(settings.seed),
        ownership=ownership,
        constraints=constraints,
    )
    work.reach(ga_work)
    phase[0] = "1 枚ずつ詰めています"
    climbed = hill_climb(
        evolved.recipe,
        pool,
        score,
        ownership=ownership,
        max_steps=settings.climb_steps,
        limit=settings.neighbours,
        rng=random.Random(settings.seed),
        similar_width=settings.similar_width,
        predict=predict,
        screen_keep=settings.screen_keep,
        constraints=constraints,
    )

    # 探索が出した値は、運で高く出た候補を選んだぶん高すぎる。別の試合で測り直す
    searched = scoreboard.get(climbed.recipe.cards, (climbed.recipe, climbed.score))[1]
    runners_up = [
        recipe
        for cards, (recipe, _) in sorted(scoreboard.items(), key=lambda item: -item[1][1])
        if cards != climbed.recipe.cards
    ][: max(0, settings.top - 1)]
    candidates = [climbed.recipe, *runners_up]
    work.reach(ga_work + climb_work)
    progress("finalize", f"探索とは別の試合で測り直しています（{len(candidates)} 件）")
    ranked = independent_evaluation(
        candidates,
        opponents,
        strategy=settings.strategy,
        games=settings.report_games,
        search_seed=settings.seed,
        cache=cache,
    )
    work.reach(work.total)
    elapsed = time.perf_counter() - started

    written = 0
    if collected and settings.use_surrogate and settings.collect_samples:
        written = append_samples(settings.store, collected)
    return ProposalReport(
        ranked=tuple(ranked),
        searched_win_rate=searched,
        climb_steps=climbed.steps,
        pool_size=len(pool),
        evaluated_matchups=cache.misses,
        cache_hits=cache.hits,
        elapsed_seconds=elapsed,
        model_note=model_note,
        samples_written=written,
    )
