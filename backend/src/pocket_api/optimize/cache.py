"""勝率キャッシュ。

最適化では同じ 2 デッキを何度も評価するので、シミュレーションの結果を覚えておく。
鍵はデッキの並び順に依らないよう正規化し、エンジン版・方策・試合数を含める
（`docs/requirements.md` 8 章の `SimMatchup`）。

デッキの書き方（行の順序、空行、余分な空白）が違うだけで別物と見なさないよう、
`pocket_engine_py.normalize_deck` を通してから鍵にする。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

import pocket_engine_py as engine


@dataclass(frozen=True)
class MatchupKey:
    """キャッシュの鍵と、デッキを入れ替えたかどうか。"""

    key: str
    """正規化した鍵。"""

    swapped: bool
    """鍵を作るときに 2 つのデッキを入れ替えたか。勝率を読むときに反転が要る。"""

    deck_a: str = ""
    """辞書順で小さいほうのデッキの識別子。"""

    deck_b: str = ""
    """もう一方のデッキの識別子。"""

    engine_revision: str = ""
    """deckgym のコミット（先頭 8 文字）。"""

    strategy: str = ""
    """方策コード。"""

    games: int = 0
    """回す試合数。"""

    seed: int = 1
    """マスターシード。"""


class MatchupStore(Protocol):
    """測った結果を残しておく先。DB を想定するが、実装は問わない。

    鍵の向き（`deck_a` < `deck_b`）に正規化された勝率をやり取りする。
    反転は呼び出し側（`MatchupCache`）が面倒を見る。
    """

    def get(self, key: MatchupKey) -> float | None:
        """記録があれば返す。無ければ `None`。"""
        ...

    def put(self, key: MatchupKey, win_rate: float) -> None:
        """結果を記録する。"""
        ...


def canonical_deck(decklist: str) -> str:
    """デッキの内容だけで決まる書き方。エネルギー行を先頭に、カード行を並べ替える。

    **行の順序はシャッフルの元になる。** 同じデッキでも書き方が違うと別の試合列になり、
    同じ相手・同じシードで勝率が 22.5% と 7.5% に割れた。鍵は順序を無視するので、
    ここで表記を揃えてからシミュレートしないと「同じ鍵なのに別の結果」になる。
    """
    lines = [line.strip() for line in engine.normalize_deck(decklist).splitlines() if line.strip()]
    energy = [line for line in lines if line.lower().startswith("energy:")]
    cards = sorted(line for line in lines if not line.lower().startswith("energy:"))
    return "\n".join(energy + cards) + "\n"


def deck_fingerprint(decklist: str) -> str:
    """デッキの内容だけで決まる短い識別子。書き方の違いは吸収する。"""
    return hashlib.sha256(canonical_deck(decklist).encode("utf-8")).hexdigest()[:16]


def matchup_key(
    deck_a: str, deck_b: str, strategy: str, games: int, *, seed: int = 1
) -> MatchupKey:
    """2 デッキの対戦を表す鍵。どちらを先に渡しても同じ鍵になる。

    シードも鍵に含める。同じシードなら全候補が同じ試合の流れ（相手の引きや先攻後攻）を
    共有するので、候補どうしの差が測りやすい（共通乱数法）。逆に、同じデッキを別の
    シードで測り直したいときもあるので、混ぜないよう鍵で分ける。
    """
    a, b = deck_fingerprint(deck_a), deck_fingerprint(deck_b)
    swapped = b < a
    if swapped:
        a, b = b, a
    revision = engine.deckgym_revision()[:8]
    return MatchupKey(
        key=f"{revision}/{strategy}/{games}/s{seed}/{a}/{b}",
        swapped=swapped,
        deck_a=a,
        deck_b=b,
        engine_revision=revision,
        strategy=strategy,
        games=games,
        seed=seed,
    )


@dataclass
class MatchupCache:
    """同じ対戦を二度シミュレートしないための覚え書き。

    プロセス内のメモリに持つ。DB への永続化は呼び出し側の責務にして、
    ここは探索ループから使いやすい形に保つ。
    """

    _rates: dict[str, float] = field(default_factory=dict)
    hits: int = 0
    """キャッシュから返した回数。"""

    misses: int = 0
    """実際にシミュレートした回数。"""

    store: MatchupStore | None = None
    """測った結果を残す先。渡すとプロセスをまたいで使い回せる。"""

    def win_rate(
        self, deck_a: str, deck_b: str, strategy: str, games: int, *, seed: int = 1
    ) -> float:
        """`deck_a` から見た勝率。同じ対戦を二度は回さない。"""
        entry = matchup_key(deck_a, deck_b, strategy, games, seed=seed)
        cached = self._rates.get(entry.key)
        if cached is None and self.store is not None:
            # 前に測ったものが残っていれば、シミュレートせずに済む
            cached = self.store.get(entry)
            if cached is not None:
                self._rates[entry.key] = cached
                self.hits += 1
        if cached is None:
            self.misses += 1
            # 鍵の向き（辞書順で小さいほうが先攻）に合わせて回す
            # 表記を揃えてから回す。行の順序が違うだけで結果が変わってはいけない
            first, second = (
                (canonical_deck(deck_b), canonical_deck(deck_a))
                if entry.swapped
                else (canonical_deck(deck_a), canonical_deck(deck_b))
            )
            result = engine.evaluate_matchup(first, second, strategy, strategy, games, seed)
            cached = result.overall.win_rate
            self._rates[entry.key] = cached
            if self.store is not None:
                self.store.put(entry, cached)
        else:
            self.hits += 1
        return 1.0 - cached if entry.swapped else cached

    def __len__(self) -> int:
        return len(self._rates)
