"""メタデッキ一覧（F-01）。

「現環境のメタデッキ一覧と使用率・勝率を閲覧したい」に応える。
受け入れ条件は「limitless のスナップショットが日付付きで表示される」ことなので、
どの時点のデータかを必ず添える。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pocket_api.api.deps import get_session
from pocket_api.api.schemas import DeckOut
from pocket_api.cards.catalog import translator
from pocket_api.db.meta import latest_snapshot, snapshot_archetypes, snapshot_decks
from pocket_api.db.models import MetaDeck, MetaSnapshot
from pocket_api.optimize.archetype import MetaArchetype

router = APIRouter(tags=["meta"])


class MetaDeckOut(BaseModel):
    """一覧に出す 1 アーキタイプ。"""

    rank: int
    name: str
    name_ja: str
    slug: str
    count: int
    share: float
    win_rate: float
    decklist: DeckOut | None
    """代表デッキリスト。読み取れなければ `None`。"""


class MetaDecksResponse(BaseModel):
    """メタデッキ一覧。どの時点のスナップショットかを添える。"""

    fetched_at: datetime
    format: str
    set: str
    decks: list[MetaDeckOut]


def latest_meta(session: Session) -> tuple[MetaSnapshot, list[MetaDeck]]:
    """最新のスナップショットと、その順位順のデッキ。無ければ 404。"""
    snapshot = latest_snapshot(session)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="メタのスナップショットがありません")
    return snapshot, snapshot_decks(session, snapshot.id)


def current_archetypes(session: Session) -> list[MetaArchetype]:
    """最新のスナップショットのアーキタイプ。スナップショットが無ければ空（名前は作って返す）。"""
    snapshot = latest_snapshot(session)
    return snapshot_archetypes(session, snapshot.id) if snapshot else []


def meta_opponents(decks: list[MetaDeck]) -> list[tuple[str, float, str]]:
    """評価の相手（名前, 使用率, 代表デッキリスト）。"""
    return [(d.name, d.share, d.decklists[0]) for d in decks if d.decklists]


def _decklist(deck: MetaDeck) -> DeckOut | None:
    if not deck.decklists:
        return None
    try:
        return DeckOut.from_text(deck.decklists[0])
    except ValueError:
        return None


@router.get("/meta/decks", response_model=MetaDecksResponse)
def list_meta_decks(session: Annotated[Session, Depends(get_session)]) -> MetaDecksResponse:
    """最新のスナップショットのメタデッキ一覧を返す。"""
    snapshot, decks = latest_meta(session)
    names = translator()
    return MetaDecksResponse(
        fetched_at=snapshot.fetched_at,
        format=snapshot.format,
        set=snapshot.set_code,
        decks=[
            MetaDeckOut(
                rank=d.rank,
                name=d.name,
                name_ja=names.text(d.name),
                slug=d.slug,
                count=d.count,
                share=d.share,
                win_rate=d.win_rate,
                decklist=_decklist(d),
            )
            for d in decks
        ],
    )
