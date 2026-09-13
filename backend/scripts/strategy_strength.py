"""方策同士をメタデッキで対戦させ、強さを測る。

これまで方策の強さは A1 の炎デッキ（玩具デッキ）でしか測っておらず、
メタデッキでの強さは分かっていなかった。

強さと実戦再現度の関係を確かめるために使う。強い方策ほど実戦に近いなら、
強化学習に投資する価値がある。無関係なら別の方向を探す必要がある。

    uv run python scripts/strategy_strength.py --games 200
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path

import pocket_engine_py as engine

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "meta" / "2026-09-07_B4a-standard.json"


def load_decks(snapshot: Path, per_archetype: int) -> list[tuple[str, str]]:
    """（アーキタイプ名, デッキリスト）の一覧を返す。"""
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    decks: list[tuple[str, str]] = []
    for row in data["archetypes"]:
        taken = 0
        for decklist in row["decklists"]:
            try:
                engine.validate_deck(decklist)
            except ValueError:
                continue
            decks.append((row["name"], decklist))
            taken += 1
            if taken >= per_archetype:
                break
    return decks


def duel(decks: list[tuple[str, str]], left: str, right: str, games: int, seed: int) -> float:
    """left 側の勝率。全デッキを両者に持たせて対戦させる。

    同じデッキ同士で戦わせるので、勝率の差は方策の差だけを表す。
    """
    score = 0.0
    decided = 0
    for index, (_, decklist) in enumerate(decks):
        result = engine.evaluate_matchup(decklist, decklist, left, right, games, seed + index)
        score += result.overall.wins + result.overall.ties * 0.5
        decided += result.overall.decided
    return score / decided if decided else 0.5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--games", type=int, default=200, help="1 デッキあたりの試合数")
    parser.add_argument(
        "--per-archetype", type=int, default=1, help="1 アーキタイプあたりのリスト数"
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--strategies", nargs="+", default=["et", "r", "aa", "er", "v", "p"])
    args = parser.parse_args()

    decks = load_decks(args.snapshot, args.per_archetype)
    print(f"{args.snapshot.name} / デッキ {len(decks)} 件 / 1 デッキ {args.games} 試合")
    print("ミラーマッチ（同じデッキ同士）で方策だけを変えて対戦させる\n")

    header = " ".join(f"{s:>7}" for s in args.strategies)
    print(f"{'方策':<6}{header}   平均")
    rates: dict[tuple[str, str], float] = {}
    started = time.perf_counter()
    for left, right in itertools.combinations(args.strategies, 2):
        rate = duel(decks, left, right, args.games, args.seed)
        rates[(left, right)] = rate
        rates[(right, left)] = 1.0 - rate

    table: dict[str, float] = {}
    for left in args.strategies:
        cells = []
        scores = []
        for right in args.strategies:
            if left == right:
                cells.append("      -")
                continue
            rate = rates[(left, right)]
            cells.append(f"{rate:7.1%}")
            scores.append(rate)
        average = sum(scores) / len(scores)
        table[left] = average
        print(f"{left:<6}{' '.join(cells)}  {average:6.1%}")

    elapsed = time.perf_counter() - started
    print(f"\n{elapsed:.1f} 秒")
    print("\n強い順:")
    for name, average in sorted(table.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<5} {average:.1%}")


if __name__ == "__main__":
    main()
