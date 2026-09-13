"""方策ごとに、シミュレーションが実戦の相性をどれだけ再現するかを比べる。

limitless の相性表（アーキタイプ同士の実戦勝率）を基準にする。
総合勝率はアーキタイプ間の差が標準偏差 3.7% しかなく、各値の信頼区間に
埋もれてしまうため基準にならない。相性表なら標準偏差 16.1% あり、
方策の良し悪しを判別できる。

対象は現環境（B4a）のスナップショット。過去セットのほうが試合数は多いが、
すでに終わった環境なので使わない。

    uv run python scripts/compare_strategies.py --games 200

フェーズ 2 の方策選定とフェーズ 3 の校正に使う（`docs/status.md`）。
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, NamedTuple

import pocket_engine_py as engine

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "meta" / "2026-09-07_B4a-standard.json"

MIN_MATCHES = 30
"""基準として使う実戦の最小試合数。これ未満は信頼区間が広すぎるので除く。"""


class Pair(NamedTuple):
    """比較対象の 1 組。"""

    a: int
    b: int
    actual: float
    matches: int


def _is_usable(decklist: str) -> bool:
    """エンジンが評価できるデッキリストか。"""
    try:
        engine.validate_deck(decklist)
    except ValueError:
        return False
    return True


def pearson(xs: list[float], ys: list[float]) -> float:
    """ピアソンの積率相関係数。"""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else float("nan")


def spearman(xs: list[float], ys: list[float]) -> float:
    """スピアマンの順位相関係数。順位だけを見るので外れ値に強い。"""

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        for rank, index in enumerate(order):
            result[index] = float(rank)
        return result

    return pearson(ranks(xs), ranks(ys))


def collect_pairs(rows: list[dict[str, Any]]) -> list[Pair]:
    """スナップショットから、実戦データのある組を集める。

    相性表は両方向に載っているため、`a < b` の向きだけを採用し、
    試合数の多い側の値を使う。
    """
    index = {row["slug"]: i for i, row in enumerate(rows)}
    best: dict[tuple[int, int], Pair] = {}

    for i, row in enumerate(rows):
        for matchup in row["matchups"]:
            j = index.get(matchup["opponent_slug"])
            if j is None or i == j or matchup["matches"] < MIN_MATCHES:
                continue
            # 常に (小さい添字, 大きい添字) の向きに正規化する
            if i < j:
                key, rate = (i, j), matchup["win_rate"]
            else:
                key, rate = (j, i), 1.0 - matchup["win_rate"]

            existing = best.get(key)
            if existing is None or matchup["matches"] > existing.matches:
                best[key] = Pair(key[0], key[1], rate, matchup["matches"])

    return sorted(best.values())


def simulate(
    decks: list[list[str]], pairs: list[Pair], strategy: str, games: int, seed: int
) -> tuple[list[float], float]:
    """各組をシミュレーションし、先に挙げたアーキタイプから見た勝率を返す。

    実戦の相性表は同一アーキタイプの構築違いをまとめた値なので、
    こちらも複数のデッキリストの組み合わせを平均して合わせる。

    組み合わせをすべて回すと 1 回あたりの試合数が細かくなり、並列化が効かない。
    さらに、すべての組み合わせに同じシードを使うと試合ごとの乱数列が共有され、
    運の偏りが全組み合わせで重なって勝率が大きくずれる（実測で 32% が 49% になった）。
    デッキリストは組ごとに巡回して選び、ラウンドごとに異なるシードを与える。
    """
    started = time.perf_counter()
    rates: list[float] = []
    for pair in pairs:
        lists_a, lists_b = decks[pair.a], decks[pair.b]
        rounds = max(len(lists_a), len(lists_b))
        per_round = max(2, games // rounds)

        score = 0.0
        decided = 0
        for index in range(rounds):
            deck_a = lists_a[index % len(lists_a)]
            deck_b = lists_b[index % len(lists_b)]
            result = engine.evaluate_matchup(
                deck_a, deck_b, strategy, strategy, per_round, seed + index
            )
            score += result.overall.wins + result.overall.ties * 0.5
            decided += result.overall.decided
        rates.append(score / decided if decided else 0.5)
    return rates, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--games", type=int, default=200, help="1 組あたりの試合数")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["p"],
        help="比較する方策コード。既定は独自方策のみ。"
        "deckgym 内蔵の方策は主役カードを場に出せず、基準として使えない",
    )
    args = parser.parse_args()

    snapshot: dict[str, Any] = json.loads(args.snapshot.read_text(encoding="utf-8"))
    # 未実装カードを含むデッキリストは評価できないので除く
    for row in snapshot["archetypes"]:
        row["decklists"] = [d for d in row["decklists"] if _is_usable(d)]
    rows = [a for a in snapshot["archetypes"] if a["decklists"]]
    decks = [a["decklists"] for a in rows]
    pairs = collect_pairs(rows)

    actual = [p.actual for p in pairs]
    mean = sum(actual) / len(actual)
    sd = (sum((a - mean) ** 2 for a in actual) / (len(actual) - 1)) ** 0.5
    lists = sum(len(d) for d in decks)
    print(f"{args.snapshot.name} / 比較対象 {len(pairs)} 組（実戦 {MIN_MATCHES} 試合以上）")
    print(f"デッキリスト {lists} 件 / {len(rows)} アーキタイプ")
    print(f"実戦勝率の幅 {min(actual):.1%}〜{max(actual):.1%} / 標準偏差 {sd:.1%}")
    print(f"1 組あたり {args.games} 試合 / シード {args.seed}\n")

    print("| 方策 | ピアソン相関 | スピアマン相関 | 平均絶対誤差 | 予測の標準偏差 | 秒 |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for strategy in args.strategies:
        predicted, elapsed = simulate(decks, pairs, strategy, args.games, args.seed)
        errors = [abs(p - a) for p, a in zip(predicted, actual, strict=True)]
        pm = sum(predicted) / len(predicted)
        psd = (sum((p - pm) ** 2 for p in predicted) / (len(predicted) - 1)) ** 0.5
        print(
            f"| `{strategy}` | {pearson(predicted, actual):+.3f} "
            f"| {spearman(predicted, actual):+.3f} "
            f"| {sum(errors) / len(errors):.1%} | {psd:.1%} | {elapsed:.1f} |"
        )

    # 参考：1 組 200 試合での標準誤差。相関の上限を左右する
    se = 0.5 / math.sqrt(args.games)
    print(f"\nシミュレーション 1 組あたりの標準誤差はおよそ ±{1.96 * se:.1%}（95%）")


if __name__ == "__main__":
    main()
