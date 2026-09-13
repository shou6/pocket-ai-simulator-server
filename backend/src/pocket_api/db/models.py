"""DB モデル（要件 9 章）。

責務の境界として、**カードのマスタデータはエンジン側に置く**（`pocket_engine_py`）。
DB に持つのは、取得したメタ環境のスナップショットと、計算結果のキャッシュ・ジョブ。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pocket_api.db.base import Base, TimestampMixin


class SimMatchup(Base, TimestampMixin):
    """シミュレーションした 2 デッキの対戦結果（勝率キャッシュ）。

    デッキは `optimize.cache.deck_fingerprint` の値で持つ。書き方の違いは吸収され、
    行の順序にも依らない。`deck_a` < `deck_b` になるよう正規化して入れる。

    再現に必要な条件（エンジン版・方策・試合数・シード）をすべて鍵に含める。
    エンジンを更新すればルールが変わりうるので、古い行は自然に使われなくなる。
    """

    __tablename__ = "sim_matchups"
    __table_args__ = (
        UniqueConstraint(
            "deck_a",
            "deck_b",
            "engine_revision",
            "strategy",
            "games",
            "seed",
            name="uq_sim_matchup_conditions",
        ),
    )

    deck_a: Mapped[str] = mapped_column(String(16), nullable=False)
    """デッキ A の識別子。辞書順で小さいほう。"""

    deck_b: Mapped[str] = mapped_column(String(16), nullable=False)
    """デッキ B の識別子。"""

    engine_revision: Mapped[str] = mapped_column(String(16), nullable=False)
    """deckgym のコミット（先頭 8 文字）。"""

    strategy: Mapped[str] = mapped_column(String(16), nullable=False)
    """方策コード。"""

    games: Mapped[int] = mapped_column(Integer, nullable=False)
    """回した試合数。"""

    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    """マスターシード。同じ値なら同じ試合列になる。"""

    wins: Mapped[float] = mapped_column(Float, nullable=False)
    """デッキ A の勝ち数。引き分けは 0.5 勝で数えるので小数になる。"""

    @property
    def win_rate(self) -> float:
        """デッキ A から見た勝率。"""
        return self.wins / self.games if self.games else 0.0


class MetaSnapshot(Base, TimestampMixin):
    """ある日のメタ環境（limitless から取得したもの）。

    取得日とフォーマットで分け、上書きせず追記する（`docs/data-sources.md` 4 節）。
    """

    __tablename__ = "meta_snapshots"
    __table_args__ = (
        UniqueConstraint("fetched_at", "format", "set_code", name="uq_meta_snapshot_key"),
    )

    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    set_code: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)

    decks: Mapped[list[MetaDeck]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="MetaDeck.rank",
    )


class MetaDeck(Base, TimestampMixin):
    """スナップショット内の 1 アーキタイプ。"""

    __tablename__ = "meta_decks"
    __table_args__ = (UniqueConstraint("snapshot_id", "slug", name="uq_meta_deck_slug"),)

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    share: Mapped[float] = mapped_column(Float, nullable=False)
    wins: Mapped[int] = mapped_column(Integer, nullable=False)
    losses: Mapped[int] = mapped_column(Integer, nullable=False)
    ties: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False)

    decklists: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    """代表デッキリスト（テキスト）。先頭を代表として使う。"""

    snapshot: Mapped[MetaSnapshot] = relationship(back_populates="decks")
    matchups: Mapped[list[MetaMatchup]] = relationship(
        back_populates="deck", cascade="all, delete-orphan"
    )


class MetaMatchup(Base, TimestampMixin):
    """実戦の相性（校正の正解データ）。"""

    __tablename__ = "meta_matchups"
    __table_args__ = (
        UniqueConstraint("deck_id", "opponent_slug", name="uq_meta_matchup_opponent"),
    )

    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_decks.id", ondelete="CASCADE"), nullable=False
    )
    opponent_slug: Mapped[str] = mapped_column(String(255), nullable=False)
    matches: Mapped[int] = mapped_column(Integer, nullable=False)
    wins: Mapped[int] = mapped_column(Integer, nullable=False)
    losses: Mapped[int] = mapped_column(Integer, nullable=False)
    ties: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False)

    deck: Mapped[MetaDeck] = relationship(back_populates="matchups")


class OptimizationJob(Base, TimestampMixin):
    """数分かかる処理（改善提案・デッキ提案）のジョブ。キューを兼ねる（要件 7 章・9 章）。

    ワーカーは `SELECT ... FOR UPDATE SKIP LOCKED` で `queued` の行を 1 件ずつ取る。
    同じ条件（`params_key`）で済んだジョブがあれば、再実行せずにその結果を返す。
    """

    __tablename__ = "optimization_jobs"
    __table_args__ = (
        Index("ix_optimization_jobs_status_created_at", "status", "created_at"),
        Index("ix_optimization_jobs_params_key", "params_key"),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    """`improve`（1 枚入れ替え）か `optimize`（デッキ提案）。"""

    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    """実行に必要な条件。"""

    params_key: Mapped[str] = mapped_column(String(64), nullable=False)
    """条件から作る鍵。エンジン版と方策を含み、同じ鍵なら同じ結果になる。"""

    engine_revision: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(16), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    """`queued` / `running` / `done` / `failed`。"""

    stage: Mapped[str | None] = mapped_column(String(16))
    """いまの段階。`prepare`（準備）/ `evaluate`（候補の評価）/ `finalize`（仕上げ）。"""

    detail: Mapped[str | None] = mapped_column(String(255))
    """段階の中で何をしているか（「候補を絞り込み中（166 通り → 20 通り）」など）。"""

    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    games_per_second: Mapped[float | None] = mapped_column(Float)
    """実行したワーカーの対戦の速さ（`jobs.speed`）。所要時間の記録を、いまのワーカーの速さに換算するのに使う。"""

    work_done: Mapped[int | None] = mapped_column(Integer)
    """済んだ仕事の量（デッキの評価 1 回を 1 とする）。`work_total` と合わせて残り時間を出す。"""

    work_total: Mapped[int | None] = mapped_column(Integer)
    """仕事の量の見込み（上限）。探索は途中で止まることがあるので、実際はこれより少なく済む。"""

    remaining_seconds: Mapped[int | None] = mapped_column(Integer)
    """最後に進捗を書いた時点の残り時間の見込み（`updated_at` からの経過を引いて使う）。"""


class WorkerSpeed(Base, TimestampMixin):
    """ワーカーが起動時に測った対戦の速さ。本番と手元でマシンが違っても所要時間の目安を合わせる。"""

    __tablename__ = "worker_speeds"

    games_per_second: Mapped[float] = mapped_column(Float, nullable=False)
    """環境の上位デッキどうしを方策 `l` で戦わせたときの 1 秒あたりの試合数。"""

    threads: Mapped[int] = mapped_column(Integer, nullable=False)
    """測ったときに使えた CPU の数（参考）。"""
