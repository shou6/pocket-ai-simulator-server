"""デッキ診断。

自分のデッキがメタ環境のどのデッキに強く、どこに弱いかを出す（要件 F-07）。
提案の根拠として「主要アーキタイプごとの勝率と信頼区間」を返す（要件 5.2 節）。

探索（`search.py` / `genetic.py`）と違い、評価するのは 1 デッキ × メタ 10 件だけなので、
校正済みの方策 `l` で丁寧に測っても現実的な時間で終わる。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pocket_engine_py as engine

from pocket_api.optimize.cache import canonical_deck
from pocket_api.optimize.evaluate import weighted_interval
from pocket_api.optimize.recipe import DeckRecipe


@dataclass(frozen=True)
class MatchupResult:
    """メタの 1 デッキとの相性。"""

    name: str
    """相手のアーキタイプ名。"""

    share: float
    """相手の使用率。"""

    win_rate: float
    """こちらから見た勝率。"""

    interval: tuple[float, float]
    """勝率の 95% 信頼区間。"""

    going_first: float
    """こちらが先攻のときの勝率。"""

    going_second: float
    """こちらが後攻のときの勝率。"""

    games: int
    """回した試合数。"""


@dataclass(frozen=True)
class Diagnosis:
    """診断の結果。"""

    overall: float
    """使用率で重み付けした期待勝率。"""

    matchups: tuple[MatchupResult, ...]
    """相手ごとの結果。勝率の高い順。"""

    strategy: str
    """使った方策。"""

    overall_going_first: float = 0.0
    """こちらが先攻のときの、使用率で重み付けした期待勝率。"""

    overall_going_second: float = 0.0
    """こちらが後攻のときの、使用率で重み付けした期待勝率。"""

    overall_interval: tuple[float, float] = (0.0, 1.0)
    """期待勝率の 95% 信頼区間。相手ごとの勝率を独立とみなして正規近似で出す。"""


def unimplemented_cards(decklist: str) -> tuple[tuple[str, str, str], ...]:
    """デッキに含まれる未実装カード。（カード ID, 名前, 理由）を返す。

    未実装カードがあるとシミュレーションできない（要件 F-08）。
    何が原因かを示せるよう、カードごとに理由を付ける。
    """
    statuses = {c.id: c for c in engine.card_statuses_all()}
    found: list[tuple[str, str, str]] = []
    for card_id, _ in DeckRecipe.from_text(decklist).cards:
        status = statuses.get(card_id)
        if status is None:
            found.append((card_id, card_id, "カードが見つからない"))
        elif not status.is_complete:
            found.append((card_id, status.name, status.description))
    return tuple(found)


def load_meta_decks(snapshot: Path) -> tuple[tuple[str, float, str], ...]:
    """メタのスナップショットから（名前, 使用率, デッキリスト）を読む。"""
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    rows = data.get("archetypes") or data.get("decks") or []
    return tuple(
        (row["name"], float(row["share"]), row["decklists"][0])
        for row in rows
        if row.get("decklists")
    )


def diagnose(
    decklist: str,
    snapshot: Path,
    *,
    strategy: str = "l",
    games: int = 200,
    seed: int = 1,
) -> Diagnosis:
    """デッキをスナップショットのメタ環境と戦わせ、相性を返す。"""
    return diagnose_against(
        decklist, load_meta_decks(snapshot), strategy=strategy, games=games, seed=seed
    )


def diagnose_against(
    decklist: str,
    opponents: Sequence[tuple[str, float, str]],
    *,
    strategy: str = "l",
    games: int = 200,
    seed: int = 1,
) -> Diagnosis:
    """デッキを（名前, 使用率, デッキリスト）の相手と戦わせ、相性を返す。

    未実装カードを含む場合は `ValueError` を送出する。呼び出し側は
    `unimplemented_cards` で原因を示す。
    """
    blocked = unimplemented_cards(decklist)
    if blocked:
        names = "、".join(f"{name}（{reason}）" for _, name, reason in blocked)
        raise ValueError(f"未実装のカードが含まれています: {names}")

    # 表記を揃えてから回す。行の順序はシャッフルの元になるので、
    # 同じデッキでも書き方が違うと別の結果になる（`cache.canonical_deck`）
    mine = canonical_deck(decklist)
    results: list[MatchupResult] = []
    for name, share, opponent in opponents:
        matchup = engine.evaluate_matchup(
            mine, canonical_deck(opponent), strategy, strategy, games, seed
        )
        low, high = matchup.overall.confidence_interval_95 or (0.0, 1.0)
        results.append(
            MatchupResult(
                name=name,
                share=share,
                win_rate=matchup.overall.win_rate,
                interval=(low, high),
                going_first=matchup.going_first.win_rate,
                going_second=matchup.going_second.win_rate,
                games=games,
            )
        )
    total_share = sum(r.share for r in results)

    def weighted(pick: Callable[[MatchupResult], float]) -> float:
        if total_share <= 0:
            return 0.0
        return sum(pick(r) * r.share for r in results) / total_share

    overall = weighted(lambda r: r.win_rate)
    results.sort(key=lambda r: r.win_rate, reverse=True)
    return Diagnosis(
        overall=overall,
        overall_interval=weighted_interval([(r.share, r.win_rate) for r in results], games),
        matchups=tuple(results),
        strategy=strategy,
        overall_going_first=weighted(lambda r: r.going_first),
        overall_going_second=weighted(lambda r: r.going_second),
    )
