"""デッキ評価。

候補デッキをメタ環境の各デッキと戦わせ、使用率で重み付けした期待勝率を返す
（`docs/requirements.md` 8 章「目的関数：使用率で重み付けした期待勝率」）。

探索では大量の候補を評価するので、勝ち目のない候補は全試合ぶん回さずに切り上げる。
その判定が `should_stop_early`。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pocket_api.optimize.cache import MatchupCache

# 逐次検定を始める最低試合数。これ未満では勝率のぶれが大きすぎて判断できない
MIN_GAMES_BEFORE_STOPPING = 20


@dataclass(frozen=True)
class Opponent:
    """メタ環境の 1 デッキ。"""

    name: str
    """アーキタイプ名。"""

    decklist: str
    """デッキリスト（テキスト）。"""

    share: float
    """使用率。合計が 1 でなくてもよく、比率として扱う。"""


@dataclass(frozen=True)
class Evaluation:
    """候補デッキの評価結果。"""

    win_rate: float
    """使用率で重み付けした期待勝率。"""

    per_opponent: tuple[tuple[str, float], ...]
    """相手ごとの勝率。"""

    games: int
    """1 組あたりに回した試合数。"""

    stopped_early: bool = False
    """目標に届かないと分かって、残りの相手を評価せずに切り上げたか。"""


def expected_win_rate(
    decklist: str,
    opponents: list[Opponent],
    *,
    strategy: str = "l",
    games: int = 200,
    cache: MatchupCache | None = None,
    seed: int = 1,
    target: float | None = None,
) -> Evaluation:
    """メタ環境に対する期待勝率。使用率で重み付けする。

    `seed` は試合の流れを決める。候補どうしを比べるときは同じ値を使う（共通乱数法）。
    同じデッキを測り直してぶれを見たいときだけ変える。

    `target` を渡すと、**残りの相手に全勝しても届かない**と分かった時点で切り上げる。
    探索は候補を何百も評価するので、見込みのないものを早く切ると大きく短縮できる。
    切り上げた場合の `win_rate` は「そこまでの結果 + 残りは全勝」の上限値になるので、
    **目標に届かないことだけが分かる**。順位付けには使えない。
    """
    if not opponents:
        raise ValueError("相手がいません")
    store = cache if cache is not None else MatchupCache()
    total_share = sum(max(o.share, 0.0) for o in opponents)
    if total_share <= 0.0:
        raise ValueError("使用率の合計が 0 です")

    rates: list[tuple[str, float]] = []
    weighted = 0.0
    remaining = total_share
    for opponent in opponents:
        share = max(opponent.share, 0.0)
        rate = store.win_rate(decklist, opponent.decklist, strategy, games, seed=seed)
        rates.append((opponent.name, rate))
        weighted += rate * share / total_share
        remaining -= share
        # 残り全部に勝ってもこの値。これが目標に届かないなら、続けても無駄
        if target is not None and weighted + remaining / total_share < target:
            return Evaluation(
                win_rate=weighted + remaining / total_share,
                per_opponent=tuple(rates),
                games=games,
                stopped_early=True,
            )
    return Evaluation(win_rate=weighted, per_opponent=tuple(rates), games=games)


def weighted_interval(
    rates: Sequence[tuple[float, float]], games: int, *, z: float = 1.96
) -> tuple[float, float]:
    """（使用率, 勝率）の加重平均の 95% 信頼区間。

    相手ごとの勝率は別々の試合から出るので独立とみなし、分散を重みの 2 乗で足す
    （正規近似）。1 組あたりの試合数は `games`。
    """
    total = sum(max(share, 0.0) for share, _ in rates)
    if total <= 0 or games <= 0:
        return (0.0, 1.0)
    mean = sum(max(share, 0.0) * rate for share, rate in rates) / total
    variance = sum(
        (max(share, 0.0) / total) ** 2 * rate * (1.0 - rate) / games for share, rate in rates
    )
    spread = z * math.sqrt(variance)
    return (max(0.0, mean - spread), min(1.0, mean + spread))


def should_stop_early(*, wins: int, games: int, target: float, confidence: float = 0.95) -> bool:
    """この候補は基準に届かないと見て、残りの試合を打ち切ってよいか。

    勝率の信頼区間の上端が基準を下回ったら、これ以上回しても覆らないとみなす。
    区間は正規近似（Wald）で出す。試合数が少ないうちは判断しない。
    """
    if games < MIN_GAMES_BEFORE_STOPPING or games <= 0:
        return False
    rate = wins / games
    # 標準誤差。勝率が 0 や 1 でも幅が 0 にならないよう下限を置く
    variance = max(rate * (1.0 - rate), 0.25 / games)
    z = _z_for(confidence)
    upper = rate + z * math.sqrt(variance / games)
    return upper < target


def _z_for(confidence: float) -> float:
    """片側信頼度に対応する標準正規分布の分位点。よく使う値だけ持つ。"""
    table = {0.90: 1.2816, 0.95: 1.6449, 0.99: 2.3263}
    return table.get(round(confidence, 2), 1.6449)
