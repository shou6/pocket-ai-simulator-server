"""デッキの改善（何枚でも入れ替える）。

自分のデッキから 1 枚ずつ入れ替えては対戦させ、上がらなくなるか `max_steps` 枚に達するまで登る
（`search.hill_climb`）。最後に、元のデッキと改善後のデッキを**探索とは別の試合**で測り直して
差を出す。

1 枚入れ替えだけの改善（`improve.suggest_improvements`）は、差（中央 1.9 点）がぶれ（±1.0 点）と
同じ桁で効果が見えにくかった。ユーザーも 1 枚に限る想定ではなかった（2026-09-13）。

探索中の値は「多数の候補から最も高く出たもの」なので実力より高く出る。画面に出すのは
測り直した値だけにする（`propose.independent_evaluation` と同じ考え方）。
"""

from __future__ import annotations

import random
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from pocket_api.optimize.cache import MatchupCache
from pocket_api.optimize.constraints import DeckConstraints
from pocket_api.optimize.diagnose import unimplemented_cards
from pocket_api.optimize.evaluate import Evaluation, Opponent, expected_win_rate
from pocket_api.optimize.features import deck_features
from pocket_api.optimize.improve import DEFAULT_STORE, load_screening_model
from pocket_api.optimize.propose import INDEPENDENT_SEED_OFFSET
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.search import full_card_pool, hill_climb

Progress = Callable[[str, str], None]


def _ignore(_stage: str, _detail: str) -> None:
    return None


@dataclass(frozen=True)
class RefineSettings:
    """改善の設定。"""

    strategy: str = "l"
    games: int = 60
    """探索中の 1 組あたりの試合数。学習データのある試合数にする。"""

    report_games: int = 200
    """測り直しの 1 組あたりの試合数。"""

    max_steps: int = 5
    """入れ替える枚数の上限。"""

    screen_keep: int = 20
    """1 手ごとに実評価する候補の数。モデルで絞った上位。"""

    neighbours: int = 40
    """モデルが無いときに、1 手ごとに実評価する候補の数の上限。"""

    similar_width: int = 16
    seed: int = 1
    store: Path = DEFAULT_STORE


@dataclass(frozen=True)
class Swap:
    """1 手の入れ替え。"""

    step: int
    out_card: str
    in_card: str


@dataclass(frozen=True)
class RefineReport:
    base: DeckRecipe
    final: DeckRecipe
    swaps: tuple[Swap, ...]
    base_evaluation: Evaluation
    """元のデッキを測り直した結果。"""

    final_evaluation: Evaluation
    """改善後のデッキを測り直した結果。入れ替えが無ければ元のデッキと同じ。"""

    evaluated: int
    """探索中に実評価したデッキの数。"""

    model_note: str


def _swap_between(before: DeckRecipe, after: DeckRecipe, step: int) -> Swap:
    old = dict(before.cards)
    new = dict(after.cards)
    out = next(cid for cid, count in before.cards if count > new.get(cid, 0))
    into = next(cid for cid, count in after.cards if count > old.get(cid, 0))
    return Swap(step=step, out_card=out, in_card=into)


def refine_deck(
    decklist: str,
    meta: Sequence[tuple[str, float, str]],
    *,
    settings: RefineSettings | None = None,
    excluded: Collection[str] = (),
    progress: Progress = _ignore,
) -> RefineReport:
    """自分のデッキを、上がらなくなるまで 1 枚ずつ入れ替えて改善する。"""
    if settings is None:
        settings = RefineSettings()
    blocked = unimplemented_cards(decklist)
    if blocked:
        names = "、".join(f"{name}（{reason}）" for _, name, reason in blocked)
        raise ValueError(f"未実装のカードが含まれています: {names}")

    opponents = [Opponent(name=name, decklist=text, share=share) for name, share, text in meta]
    base = DeckRecipe.from_text(decklist)
    constraints = DeckConstraints.of(excluded=excluded)
    cache = MatchupCache()
    evaluated: set[tuple[tuple[str, int], ...]] = set()

    def score(recipe: DeckRecipe, step: int) -> float:
        if step == 0 and recipe.cards == base.cards:
            progress("prepare", "元のデッキを測っています")
        else:
            evaluated.add(recipe.cards)
            progress(
                "evaluate",
                f"{step + 1} 枚目の入れ替えを探しています（{len(evaluated)} 通りを評価）",
            )
        # 候補どうしを同じ試合の流れで比べる（共通乱数法）。手番でシードは変えない
        return expected_win_rate(
            recipe.to_text(),
            opponents,
            strategy=settings.strategy,
            games=settings.games,
            cache=cache,
            seed=settings.seed,
        ).win_rate

    pool = tuple(
        card_id for card_id in full_card_pool(base.energy) if not constraints.is_excluded(card_id)
    )
    model, note = load_screening_model(settings.store, settings.strategy, settings.games)
    predict: Callable[[DeckRecipe], float] | None = None
    if model is not None:
        trained = model

        def predict_with_model(recipe: DeckRecipe) -> float:
            return trained.predict(deck_features(recipe.to_text()))

        predict = predict_with_model

    swaps: list[Swap] = []

    def on_move(step: int, before: DeckRecipe, after: DeckRecipe, _value: float) -> None:
        swaps.append(_swap_between(before, after, step + 1))

    climbed = hill_climb(
        base,
        pool,
        score,
        max_steps=settings.max_steps,
        limit=settings.neighbours,
        rng=random.Random(settings.seed),
        similar_width=settings.similar_width,
        predict=predict,
        screen_keep=settings.screen_keep,
        constraints=constraints if not constraints.empty else None,
        on_move=on_move,
    )

    progress("finalize", "探索とは別の試合で、元のデッキと改善後を測り直しています")

    def remeasure(recipe: DeckRecipe) -> Evaluation:
        return expected_win_rate(
            recipe.to_text(),
            opponents,
            strategy=settings.strategy,
            games=settings.report_games,
            cache=cache,
            seed=settings.seed + INDEPENDENT_SEED_OFFSET,
        )

    base_evaluation = remeasure(base)
    final_evaluation = base_evaluation if not swaps else remeasure(climbed.recipe)
    return RefineReport(
        base=base,
        final=climbed.recipe,
        swaps=tuple(swaps),
        base_evaluation=base_evaluation,
        final_evaluation=final_evaluation,
        evaluated=len(evaluated),
        model_note=note,
    )
