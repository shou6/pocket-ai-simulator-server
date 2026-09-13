"""メタデッキ同士の相性表を作り、方策を比較する。

`docs/requirements.md` 4.3 節の構造的な妥当性（ミラーマッチが 50% 付近、
先攻後攻の勝率差）を確認し、フェーズ 2 の方策選定に使う。

    uv run python scripts/meta_matchups.py --strategy aa --games 200
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path
from typing import Any

import pocket_engine_py as engine

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "meta" / "2026-09-07_B4a-standard.json"


def load_archetypes(path: Path, top: int) -> list[dict[str, Any]]:
    """スナップショットから、デッキリストのあるアーキタイプを読む。"""
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    rows = [a for a in snapshot["archetypes"] if a["decklist"]]
    return rows[:top]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--strategy", default="aa", help="両者に使う方策コード")
    parser.add_argument("--games", type=int, default=200, help="1 組あたりの試合数")
    parser.add_argument("--top", type=int, default=10, help="上位いくつを対象にするか")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    archetypes = load_archetypes(args.snapshot, args.top)
    names = [a["name"] for a in archetypes]
    decks = [a["decklist"] for a in archetypes]

    started = time.perf_counter()
    # 自分自身との対戦（ミラーマッチ）も含めて総当たりする
    pairs = list(itertools.combinations_with_replacement(range(len(decks)), 2))
    results: dict[tuple[int, int], Any] = {}
    for i, j in pairs:
        results[(i, j)] = engine.evaluate_matchup(
            decks[i], decks[j], args.strategy, args.strategy, args.games, args.seed
        )
    elapsed = time.perf_counter() - started
    total_games = len(pairs) * args.games

    width = max(len(n) for n in names[:6]) if names else 10
    print(f"方策 {args.strategy} / 1 組 {args.games} 試合 / シード {args.seed}")
    per_second = total_games / elapsed
    print(f"{len(pairs)} 組 {total_games} 試合を {elapsed:.1f} 秒（毎秒 {per_second:,.0f} 試合）\n")

    header = "".join(f"{j:>7}" for j in range(len(decks)))
    print(f"{'':{width}}{header}   総合")
    for i, name in enumerate(names):
        cells = []
        scores = []
        for j in range(len(decks)):
            if (i, j) in results:
                rate = results[(i, j)].overall.win_rate
            else:
                rate = results[(j, i)].overall.win_rate
                rate = None if rate is None else 1.0 - rate
            cells.append("     -" if rate is None else f"{rate:7.1%}")
            if rate is not None and i != j:
                scores.append(rate)
        overall = sum(scores) / len(scores) if scores else float("nan")
        print(f"{name[:width]:{width}}{''.join(cells)}  {overall:6.1%}")

    mirrors = [results[(i, i)].overall.win_rate for i in range(len(decks))]
    mirrors = [m for m in mirrors if m is not None]
    print(f"\nミラーマッチの勝率: 最小 {min(mirrors):.1%} / 最大 {max(mirrors):.1%}")

    # 先攻側から見た勝ち点を全対戦で足し上げる。going_second は評価対象デッキが
    # 後攻だったときの成績なので、先攻側の勝ち点は「決着数 - その勝ち点」になる
    first_score = 0.0
    first_decided = 0
    for result in results.values():
        first_score += result.going_first.wins + result.going_first.ties * 0.5
        first_decided += result.going_first.decided
        second = result.going_second
        first_score += second.decided - (second.wins + second.ties * 0.5)
        first_decided += second.decided
    if first_decided:
        print(f"先攻の勝率 {first_score / first_decided:.1%}（{first_decided} 試合）")

    unfinished = sum(r.overall.unfinished for r in results.values())
    ties = sum(r.overall.ties for r in results.values())
    print(f"引き分け {ties} 試合 / 決着せず {unfinished} 試合（全 {total_games} 試合）")


if __name__ == "__main__":
    main()
