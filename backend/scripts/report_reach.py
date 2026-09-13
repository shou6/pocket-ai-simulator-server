"""絞り込みが「本当の当たり」に届いているかを測る。

1 枚入れ替えの候補を**全件**実評価して真の勝率をそろえ、そのうえで探索の手順
（間引き → モデルで並べ替え → 上位だけ実評価）だけを何通りものシードで回す。
シミュレーションは 1 回だけで済むので、設定を変えたときの効きをすぐ比べられる。

`docs/analysis/card-pool-expansion.md` の測定に使ったもの。

    # 1 回目は全候補を実評価する（候補数 × 20 秒ほど）
    uv run python scripts/report_reach.py --deck ../data/decks/elf-bazooka.txt

    # 2 回目以降は貯めた勝率を使うので数秒で終わる
    uv run python scripts/report_reach.py --deck ../data/decks/elf-bazooka.txt \
        --width 16 --per-slot 2 4 99 --keep 5 20 40
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from pocket_api.optimize.cache import MatchupCache
from pocket_api.optimize.diagnose import load_meta_decks
from pocket_api.optimize.evaluate import Opponent, expected_win_rate
from pocket_api.optimize.features import deck_features
from pocket_api.optimize.improve import DEFAULT_STORE, _load_model, diversify
from pocket_api.optimize.recipe import DeckRecipe
from pocket_api.optimize.screening import screen
from pocket_api.optimize.search import full_card_pool, neighbours

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "meta" / "2026-09-07_B4a-standard.json"
DEFAULT_TRUTH_DIR = REPO_ROOT / "data" / "reach"


def swapped(base: DeckRecipe, candidate: DeckRecipe) -> tuple[str, str]:
    """基準と候補から（抜いた札, 入れた札）を返す。"""
    before, after = dict(base.cards), dict(candidate.cards)
    return (
        next(cid for cid, n in before.items() if after.get(cid, 0) < n),
        next(cid for cid, n in after.items() if before.get(cid, 0) < n),
    )


def collect_truth(
    base: DeckRecipe,
    candidates: tuple[DeckRecipe, ...],
    opponents: list[Opponent],
    known: dict[str, float],
    *,
    strategy: str,
    games: int,
    seed: int,
) -> dict[str, float]:
    """候補の真の勝率。すでに測ってあるものは測り直さない。"""
    cache = MatchupCache()
    missing = [c for c in candidates if _key(base, c) not in known]
    if missing:
        print(f"実評価する候補 {len(missing)} 件", flush=True)
    started = time.perf_counter()
    for index, candidate in enumerate(missing, 1):
        rate = expected_win_rate(
            candidate.to_text(),
            opponents,
            strategy=strategy,
            games=games,
            cache=cache,
            seed=seed,
        ).win_rate
        known[_key(base, candidate)] = rate
        out_id, in_id = swapped(base, candidate)
        print(f"  {index}/{len(missing)} {out_id} -> {in_id}: {rate * 100:.1f}%", flush=True)
    if missing:
        print(f"{len(missing)} 件 / {time.perf_counter() - started:.0f} 秒", flush=True)
    return known


def _key(base: DeckRecipe, candidate: DeckRecipe) -> str:
    out_id, in_id = swapped(base, candidate)
    return f"{out_id}->{in_id}"


def reach(
    base: DeckRecipe,
    pool: tuple[str, ...],
    truth: dict[str, float],
    *,
    width: int,
    limit: int | None,
    keep: int,
    per_slot: int | None,
    seeds: int,
) -> tuple[float, float, float]:
    """（届いた勝率の平均, 最良に届いた割合, その設定での最良）。

    モデルの予測だけを使い、実評価は `truth` を引いて済ませる。
    """
    model, _ = _load_model(DEFAULT_STORE, "l", 200)
    every = neighbours(base, pool, similar_width=width)
    scores = {c.cards: truth[_key(base, c)] for c in every}
    best = max(scores.values())
    if model is None:
        predict = lambda recipe: random.Random(hash(recipe.cards) & 0xFFFF).random()  # noqa: E731
    else:
        cached = {c.cards: float(model.predict(deck_features(c.to_text()))) for c in every}
        predict = lambda recipe: cached[recipe.cards]  # noqa: E731

    reached: list[float] = []
    for seed in range(seeds):
        found = neighbours(base, pool, limit=limit, rng=random.Random(seed), similar_width=width)
        if per_slot is not None:
            found = diversify(found, base, predict, per_slot=per_slot)
        picked = screen(found, predict, lambda recipe: scores[recipe.cards], keep=keep)
        reached.append(max(rate for _, rate in picked))
    hit = sum(1 for r in reached if r >= best - 1e-9) / len(reached)
    return sum(reached) / len(reached), hit, best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, required=True, help="測るデッキファイル")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--strategy", default="l")
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1, help="実評価のシード")
    parser.add_argument(
        "--width", type=int, nargs="+", default=[16], help="similar_width（複数可）"
    )
    parser.add_argument(
        "--limit",
        type=int,
        nargs="+",
        default=[0],
        help="モデルに渡す前の間引き。0 は間引かない（複数可）",
    )
    parser.add_argument(
        "--keep", type=int, nargs="+", default=[5, 20, 40], help="実評価に回す件数（複数可）"
    )
    parser.add_argument(
        "--per-slot",
        type=int,
        nargs="+",
        default=[0],
        help="抜く札ごとの上限。0 は上限なし（複数可）",
    )
    parser.add_argument("--seeds", type=int, default=40, help="手順を回す回数")
    parser.add_argument(
        "--truth-dir", type=Path, default=DEFAULT_TRUTH_DIR, help="真の勝率を貯める場所"
    )
    args = parser.parse_args(argv)

    text = args.deck.read_text(encoding="utf-8")
    base = DeckRecipe.from_text(text)
    pool = full_card_pool(base.energy)
    opponents = [
        Opponent(name=name, decklist=deck, share=share)
        for name, share, deck in load_meta_decks(args.snapshot)
    ]

    args.truth_dir.mkdir(parents=True, exist_ok=True)
    truth_path = args.truth_dir / f"{args.deck.stem}-{args.strategy}-{args.games}.json"
    known: dict[str, float] = (
        json.loads(truth_path.read_text(encoding="utf-8")) if truth_path.exists() else {}
    )

    # 比べたい幅すべての候補をまとめて実評価しておく
    candidates: dict[tuple[tuple[str, int], ...], DeckRecipe] = {}
    for width in args.width:
        for candidate in neighbours(base, pool, similar_width=width):
            candidates[candidate.cards] = candidate
    known = collect_truth(
        base,
        tuple(candidates.values()),
        opponents,
        known,
        strategy=args.strategy,
        games=args.games,
        seed=args.seed,
    )
    truth_path.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding="utf-8")

    base_rate = expected_win_rate(
        text, opponents, strategy=args.strategy, games=args.games, seed=args.seed
    ).win_rate
    print(f"\n基準 {base_rate * 100:.1f}%（{args.deck.name}）")
    print(
        f"{'幅':>3} {'間引き':>6} {'per_slot':>8} {'実評価':>5}"
        f" {'届いた平均':>10} {'最良に届く':>10}"
    )
    for width in args.width:
        for limit in args.limit:
            for per_slot in args.per_slot:
                for keep in args.keep:
                    average, hit, best = reach(
                        base,
                        pool,
                        known,
                        width=width,
                        limit=limit or None,
                        keep=keep,
                        per_slot=per_slot or None,
                        seeds=args.seeds,
                    )
                    print(
                        f"{width:>3} {limit or 'なし':>6} {per_slot or 'なし':>8} {keep:>5}"
                        f" {average * 100:>9.1f}% {hit * 100:>9.0f}%"
                    )
        print(f"   （幅 {width} の最良 {best * 100:.1f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
