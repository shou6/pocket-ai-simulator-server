"""サロゲートモデルの学習データを集める CLI。

メタデッキとその変異を評価して貯める。探索を回すほど貯まる設計だが、
最初の学習にはまとまった量が要るので、この CLI で先に集めておく
（`docs/adr/0004-surrogate-model.md`）。

    uv run python -m pocket_api.optimize.collect_cli --per-deck 20 --games 60
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import pocket_engine_py as engine

from pocket_api.ingest.snapshot import latest_snapshot_path
from pocket_api.optimize.cache import MatchupCache
from pocket_api.optimize.cli import build_opponents, load_meta
from pocket_api.optimize.collect import plan_decks
from pocket_api.optimize.dataset import Sample, append_samples, load_samples
from pocket_api.optimize.evaluate import expected_win_rate
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.search import full_card_pool

REPO_ROOT = Path(__file__).resolve().parents[4]
# 既定は data/meta のいちばん新しいスナップショット（メタを更新したら自動で追従する）
DEFAULT_SNAPSHOT = latest_snapshot_path(REPO_ROOT / "data" / "meta")
DEFAULT_STORE = REPO_ROOT / "data" / "surrogate" / "samples.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE, help="貯める先")
    parser.add_argument("--strategy", default="p", help="評価に使う方策（既定は速い p）")
    parser.add_argument("--games", type=int, default=60, help="1 組あたりの試合数")
    parser.add_argument(
        "--per-deck",
        type=int,
        default=20,
        help="メタデッキ 1 つあたり、何個のデッキを評価するか",
    )
    parser.add_argument(
        "--extra-deck",
        type=Path,
        action="append",
        default=[],
        help="メタ以外に、学習データの出発点にするデッキファイル（複数可）",
    )
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    decks = load_meta(args.snapshot)
    opponents = build_opponents(decks)
    rng = random.Random(args.seed)
    cache = MatchupCache()

    before = len(load_samples(args.store))
    started = time.perf_counter()
    # 版が想定と違うと、同じ鍵の古いデータと重複扱いになって 1 件も入らない。
    # バインディングを再ビルドし忘れたときに気づけるよう、最初に出す
    print(
        f"エンジン版 {engine.deckgym_revision()[:8]} / 方策 {args.strategy} / {args.games} 試合",
        flush=True,
    )
    collected: list[Sample] = []
    # メタデッキに、指定されたデッキを足す。探索する場所の近くを学習させるため
    decklists = [d.decklist for d in decks]
    decklists += [path.read_text(encoding="utf-8") for path in args.extra_deck]
    for decklist in decklists:
        recipe = DeckRecipe.from_text(decklist)
        pool = full_card_pool(recipe.energy)
        for candidate in plan_decks(recipe, pool, args.per_deck, rng=rng):
            text = candidate.to_text()
            rate = expected_win_rate(
                text,
                opponents,
                strategy=args.strategy,
                games=args.games,
                cache=cache,
            ).win_rate
            collected.append(Sample.of(text, rate, strategy=args.strategy, games=args.games))

    written = append_samples(args.store, collected)
    elapsed = time.perf_counter() - started
    print(f"{args.store} に {written} 件を足しました（{before} → {before + written} 件）")
    print(f"評価した対戦 {cache.misses} 組 / キャッシュ命中 {cache.hits} 回 / {elapsed:.0f} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
