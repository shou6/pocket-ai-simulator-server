"""改善提案。

自分のデッキに対して「どのカードを入れ替えると何%上がるか」を示す（要件 F-03 / F-07）。

1 枚入れ替えの候補は数百通りになる。モデルの予測で足切りし、残りだけ実評価して並べる。
モデルには「明らかに劣る候補を捨てる力はあるが、上位の中から最良を選ぶ精度はない」ので、
絞りすぎると良い候補を落とす（`docs/status.md` 3.14）。
"""

from __future__ import annotations

import random
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pocket_engine_py as engine

from pocket_api.optimize.cache import MatchupCache
from pocket_api.optimize.dataset import load_samples
from pocket_api.optimize.diagnose import load_meta_decks, unimplemented_cards
from pocket_api.optimize.evaluate import Opponent, expected_win_rate
from pocket_api.optimize.features import deck_features
from pocket_api.optimize.recipe import DeckRecipe, Ownership
from pocket_api.optimize.screening import screen
from pocket_api.optimize.search import full_card_pool, neighbours
from pocket_api.optimize.surrogate import BoostedSurrogate

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_STORE = REPO_ROOT / "data" / "surrogate" / "samples.jsonl"

Progress = Callable[[str, str], None]
"""進み具合の報告先。（段階, 説明）を受け取る。段階は `prepare` / `evaluate` / `finalize`。"""


def _ignore_progress(_stage: str, _detail: str) -> None:
    return None


MIN_SAMPLES_FOR_MODEL = 100
"""モデルを使うのに要る学習データの数。これ未満では実評価より当てにならない。"""


@dataclass(frozen=True)
class Improvement:
    """1 枚の入れ替えと、その効果。"""

    out_card: str
    """抜くカードの名前。"""

    in_card: str
    """入れるカードの名前。"""

    win_rate: float
    """入れ替えた後の期待勝率。"""

    delta: float
    """元のデッキとの差。"""

    decklist: str
    """入れ替えた後のデッキ。"""

    out_card_id: str = ""
    """抜くカードの ID。"""

    in_card_id: str = ""
    """入れるカードの ID。持っていないと言われたときに代わりを探すのに使う。"""


@dataclass(frozen=True)
class ImprovementReport:
    """改善提案の結果。"""

    base_win_rate: float
    """元のデッキの期待勝率。"""

    suggestions: tuple[Improvement, ...]
    """効果の大きい順の提案。"""

    evaluated: int
    """実評価した候補の数。"""

    screened: int
    """モデルで絞る前の候補の数。"""

    model_note: str = ""
    """絞り込みに何を使ったか。学習データの条件を明かすために持つ。"""


def _removed_card(base: DeckRecipe, candidate: DeckRecipe) -> str | None:
    """`candidate` が `base` から抜いた 1 枚。"""
    counts = dict(candidate.cards)
    return next(
        (cid for cid, n in base.cards if n > counts.get(cid, 0)),
        None,
    )


def diversify(
    candidates: Sequence[DeckRecipe],
    base: DeckRecipe,
    predict: Callable[[DeckRecipe], float],
    *,
    per_slot: int = 2,
) -> tuple[DeckRecipe, ...]:
    """同じ札を抜く案が並びすぎないよう、抜く札ごとに上位だけ残す。

    予測は「どの札を抜くか」でほぼ決まるので、素直に上位を取ると同じ 1 枚を抜く案が
    並ぶ（こいぬ総動員では 5 件すべてが「小さなふうせん を抜く」だった）。

    **絞り込みには使わない。** ここで削ると候補が「札の種類数 × per_slot」で
    頭打ちになり、当たりがその外に落ちる。実測では到達できる勝率が
    36.7% → 32.2% に下がっていた（`docs/analysis/card-pool-expansion.md`）。
    散らすのは実評価が済んだあと、**見せる側**でやる。
    """
    ranked = sorted(candidates, key=predict, reverse=True)
    used: dict[str | None, int] = {}
    kept: list[DeckRecipe] = []
    for candidate in ranked:
        slot = _removed_card(base, candidate)
        if used.get(slot, 0) >= per_slot:
            continue
        used[slot] = used.get(slot, 0) + 1
        kept.append(candidate)
    return tuple(kept)


def _spread_over_slots(suggestions: Sequence[Improvement], *, per_slot: int) -> list[Improvement]:
    """見せる案を、抜く札ごとに `per_slot` 件までに絞る。

    実評価が済んだあとに掛ける。同じ 1 枚を抜く案ばかり並ぶと選びようがない。
    """
    used: dict[str, int] = {}
    kept: list[Improvement] = []
    for item in suggestions:
        if used.get(item.out_card, 0) >= per_slot:
            continue
        used[item.out_card] = used.get(item.out_card, 0) + 1
        kept.append(item)
    return kept


def _card_names(card_ids: list[str]) -> dict[str, str]:
    """カード ID から名前を引く。"""
    return {card.id: card.name for card in engine.card_details(sorted(set(card_ids)))}


def suggest_improvements(
    decklist: str,
    snapshot: Path,
    **options: Any,
) -> ImprovementReport:
    """スナップショットのメタ環境に対して、1 枚入れ替えの改善案を返す。

    引数は `suggest_improvements_against` と同じ。
    """
    return suggest_improvements_against(decklist, load_meta_decks(snapshot), **options)


def suggest_improvements_against(
    decklist: str,
    meta: Sequence[tuple[str, float, str]],
    *,
    strategy: str = "l",
    games: int = 200,
    candidates: int = 20,
    neighbours_limit: int | None = None,
    ownership: Ownership | None = None,
    store: Path = DEFAULT_STORE,
    similar_width: int = 16,
    per_slot: int = 2,
    seed: int = 1,
    excluded: Collection[str] = (),
    progress: Progress = _ignore_progress,
) -> ImprovementReport:
    """1 枚入れ替えの改善案を返す。`meta` は（名前, 使用率, デッキリスト）。

    # 引数

    - `candidates`：実評価に回す候補の数。ここが結果をいちばん左右する。
      モデルの並べ替えは粗いので、上位 5 件では当たりを取りこぼす（実測で
      31.5%、無作為より悪い）。20 件で 35.4%、40 件で 36.7%（当たりに到達）。
      1 件あたり 200 試合で 20 秒ほど（`docs/analysis/card-pool-expansion.md`）
    - `neighbours_limit`：モデルに渡す前に候補を間引く数。既定は間引かない。
      モデルで並べ替えるのは速いので、間引いても実評価の回数は変わらず、
      当たりを取りこぼすだけだった（`docs/analysis/card-pool-expansion.md`）
    - `per_slot`：同じ札を抜く案をいくつまで実評価に回すか
    - `excluded`：入れる候補にしないカード ID（「持っていない」と言われたカード）
    - `progress`：段階が進むたびに呼ぶ
    """
    blocked = unimplemented_cards(decklist)
    if blocked:
        names = "、".join(f"{name}（{reason}）" for _, name, reason in blocked)
        raise ValueError(f"未実装のカードが含まれています: {names}")

    opponents = [Opponent(name=name, decklist=text, share=share) for name, share, text in meta]
    cache = MatchupCache()
    base = DeckRecipe.from_text(decklist)

    def evaluate(recipe: DeckRecipe) -> float:
        return expected_win_rate(
            recipe.to_text(),
            opponents,
            strategy=strategy,
            games=games,
            cache=cache,
            seed=seed,
        ).win_rate

    progress("prepare", "元のデッキを測っています")
    base_rate = evaluate(base)
    progress("prepare", "入れ替えの候補を作っています")
    blocked_ids = set(excluded)
    pool = tuple(
        card_id
        for card_id in full_card_pool(base.energy, ownership=ownership)
        if card_id not in blocked_ids
    )
    found = neighbours(
        base,
        pool,
        ownership=ownership,
        limit=neighbours_limit,
        rng=random.Random(seed),
        similar_width=similar_width,
    )
    if not found:
        return ImprovementReport(base_win_rate=base_rate, suggestions=(), evaluated=0, screened=0)

    model, note = load_screening_model(store, strategy, games)
    keep = min(candidates, len(found))
    progress("evaluate", f"候補を絞り込み中（{len(found)} 通り → {keep} 通り）")
    if model is None:
        # モデルがなければ、候補をそのまま実評価する（件数は candidates で抑える）
        scored = [(recipe, evaluate(recipe)) for recipe in found[:candidates]]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        pairs = tuple(scored)
    else:

        def predict(recipe: DeckRecipe) -> float:
            return model.predict(deck_features(recipe.to_text()))

        pairs = screen(found, predict, evaluate, keep=candidates)

    progress("finalize", "結果をまとめています")
    base_counts = dict(base.cards)
    card_names = _card_names(
        [cid for cid, _ in base.cards] + [cid for r, _ in pairs for cid, _ in r.cards]
    )
    suggestions: list[Improvement] = []
    for recipe, rate in pairs:
        counts = dict(recipe.cards)
        out = _removed_card(base, recipe)
        into = next((cid for cid in counts if counts[cid] > base_counts.get(cid, 0)), None)
        if out is None or into is None:
            continue
        suggestions.append(
            Improvement(
                out_card=card_names.get(out, out),
                in_card=card_names.get(into, into),
                win_rate=rate,
                delta=rate - base_rate,
                decklist=recipe.to_text(),
                out_card_id=out,
                in_card_id=into,
            )
        )
    suggestions.sort(key=lambda item: item.delta, reverse=True)
    suggestions = _spread_over_slots(suggestions, per_slot=per_slot)
    return ImprovementReport(
        base_win_rate=base_rate,
        suggestions=tuple(suggestions),
        evaluated=len(pairs),
        screened=len(found),
        model_note=note,
    )


def load_screening_model(
    store: Path, strategy: str, games: int
) -> tuple[BoostedSurrogate | None, str]:
    """学習データからモデルを作る。モデルと、何を使ったかの説明を返す。

    方策が違うデータは混ぜない。方策 `p` と `l` では同じデッキの見積もりが 10 ポイント
    ずれるので、物差しが変わってしまう。

    試合数が違うデータを混ぜるのは最後の手段。ラベルのぶれが試合数で決まるので、
    40 試合のデータ（ぶれ約 2.2%）では 1 枚入れ替えの伸び（3〜4%）を教えられない。
    実際、40 試合のデータで学習したモデルは山登りの当たり手を捨て、
    伸びが +6.3% から +3.3% に半減した（ADR 0004 の 2026-09-10 追記）。
    探索と同じ試合数のデータを用意すること。
    """
    exact = load_samples(store, strategy=strategy, games=games)
    if len(exact) >= MIN_SAMPLES_FOR_MODEL:
        model = BoostedSurrogate.fit([(s.features, s.win_rate) for s in exact])
        return model, f"学習データ {len(exact)} 件（方策 {strategy} / {games} 試合）"
    same_strategy = load_samples(store, strategy=strategy)
    if len(same_strategy) < MIN_SAMPLES_FOR_MODEL:
        return None, (
            f"学習データが {len(same_strategy)} 件しかないので使わない"
            f"（方策 {strategy} で {MIN_SAMPLES_FOR_MODEL} 件以上要る）"
        )
    counts = sorted({s.games for s in same_strategy})
    model = BoostedSurrogate.fit([(s.features, s.win_rate) for s in same_strategy])
    return model, (
        f"学習データ {len(same_strategy)} 件（方策 {strategy} / "
        f"{'・'.join(str(c) for c in counts)} 試合）。"
        f"{games} 試合ぴったりのデータは {len(exact)} 件しかないので、"
        "試合数の違うものも混ぜて並べ替えにだけ使った"
    )
