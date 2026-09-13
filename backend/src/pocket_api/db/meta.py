"""メタ環境のスナップショットの読み出し。API とワーカーの両方から使う。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from pocket_api.db.models import MetaDeck, MetaSnapshot
from pocket_api.optimize.archetype import MetaArchetype, meta_archetypes


def latest_snapshot(session: Session) -> MetaSnapshot | None:
    """いちばん新しいスナップショット。"""
    return session.scalar(select(MetaSnapshot).order_by(MetaSnapshot.fetched_at.desc()))


def snapshot_decks(session: Session, snapshot_id: uuid.UUID) -> list[MetaDeck]:
    """スナップショットのデッキを順位順に。"""
    return list(
        session.scalars(
            select(MetaDeck).where(MetaDeck.snapshot_id == snapshot_id).order_by(MetaDeck.rank)
        ).all()
    )


_ARCHETYPES: dict[uuid.UUID, list[MetaArchetype]] = {}


def snapshot_archetypes(session: Session, snapshot_id: uuid.UUID) -> list[MetaArchetype]:
    """スナップショットのアーキタイプの定義。

    スナップショットは追記だけで中身が変わらないので、ID ごとに覚えておく。
    """
    cached = _ARCHETYPES.get(snapshot_id)
    if cached is None:
        decks = snapshot_decks(session, snapshot_id)
        cached = meta_archetypes([(d.name, d.slug, d.decklists) for d in decks])
        _ARCHETYPES[snapshot_id] = cached
    return cached
