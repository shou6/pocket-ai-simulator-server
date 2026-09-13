"""勝率キャッシュの永続化。

`optimize.cache.MatchupCache` はプロセス内のメモリにしか持たないので、CLI を回すたびに
同じ対戦を計算し直していた。1 組 200 試合で約 18 秒かかるため、これは無視できない。

`optimize` パッケージは DB に依存させない（DB なしでも CLI が動くようにする）ので、
`MatchupStore` プロトコルの実装としてこちらに置く。
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from pocket_api.db.models import SimMatchup
from pocket_api.optimize.cache import MatchupKey


class DbMatchupStore:
    """`SimMatchup` テーブルを使う `MatchupStore` の実装。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: MatchupKey) -> float | None:
        """記録があれば勝率を返す。鍵の向きに正規化された値。"""
        row = self._session.scalar(self._query(key))
        return row.win_rate if row is not None else None

    def put(self, key: MatchupKey, win_rate: float) -> None:
        """結果を記録する。同じ条件の行があれば上書きする。"""
        row = self._session.scalar(self._query(key))
        wins = win_rate * key.games
        if row is not None:
            row.wins = wins
            return
        self._session.add(
            SimMatchup(
                deck_a=key.deck_a,
                deck_b=key.deck_b,
                engine_revision=key.engine_revision,
                strategy=key.strategy,
                games=key.games,
                seed=key.seed,
                wins=wins,
            )
        )

    @staticmethod
    def _query(key: MatchupKey) -> Select[tuple[SimMatchup]]:
        return select(SimMatchup).where(
            SimMatchup.deck_a == key.deck_a,
            SimMatchup.deck_b == key.deck_b,
            SimMatchup.engine_revision == key.engine_revision,
            SimMatchup.strategy == key.strategy,
            SimMatchup.games == key.games,
            SimMatchup.seed == key.seed,
        )
