"""方策ごとのシミュレーション速度を実測する。

`docs/requirements.md` 4.4 節の見積もりを実測値に置き換えるために使う。
release ビルドのバインディング（`make engine-py-release`）で実行すること。

    uv run python scripts/bench_matchup.py
"""

from __future__ import annotations

import argparse
import os
import time

import pocket_engine_py as engine

FIRE_DECK = """\
Energy: Fire
2 A1 042
2 A1 043
2 A1 044
2 A1 049
2 A1 050
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
"""

WATER_DECK = """\
Energy: Water
2 A1 053
2 A1 054
2 A1 055
2 A1 219
2 A1 220
2 A1 225
2 P-A 001
2 P-A 002
2 P-A 005
2 P-A 007
"""

# 方策コードと、その方策で回す試合数。遅い方策は試合数を減らす
PLANS: list[tuple[str, int]] = [
    ("et", 2000),
    ("r", 2000),
    ("aa", 2000),
    ("w", 2000),
    ("er", 2000),
    ("v", 500),
    ("e:3", 100),
    ("m", 20),
    # 独自方策。`p` は静的な採点、`l` は `p` で番の終わりまで進めて比べる先読み
    ("p", 2000),
    ("l", 500),
]


def measure(strategy: str, games: int) -> tuple[float, float]:
    """指定の方策で対戦し、（毎秒の試合数, 勝率）を返す。"""
    started = time.perf_counter()
    result = engine.evaluate_matchup(FIRE_DECK, WATER_DECK, strategy, strategy, games, 1)
    elapsed = time.perf_counter() - started
    return games / elapsed, result.overall.win_rate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="この方策コードだけ測る")
    args = parser.parse_args()

    plans = [p for p in PLANS if args.only is None or p[0] == args.only]

    print(f"engine {engine.engine_version()} / deckgym {engine.deckgym_revision()[:8]}")
    print(f"論理コア数: {os.cpu_count()}")
    print()
    print("| 方策 | 試合数 | 毎秒の試合数 | 炎デッキの勝率 |")
    print("| --- | ---: | ---: | ---: |")
    for strategy, games in plans:
        per_second, win_rate = measure(strategy, games)
        rate = "-" if win_rate is None else f"{win_rate:.3f}"
        print(f"| `{strategy}` | {games} | {per_second:,.0f} | {rate} |")


if __name__ == "__main__":
    main()
