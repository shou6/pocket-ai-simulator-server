"""カード検索と、役割の近いカード（F-09 / F-10）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pocket_api.api.schemas import CardOut
from pocket_api.cards.catalog import card_view, search_cards
from pocket_api.optimize.similar import similar_cards

router = APIRouter(prefix="/cards", tags=["cards"])


class CardsOut(BaseModel):
    cards: list[CardOut]


@router.get("", response_model=CardsOut)
def search(
    q: Annotated[str, Query(min_length=1, description="和名または英名の一部")],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> CardsOut:
    """名前でカードを探す。再録はまとめて代表の 1 枚だけ返す。"""
    return CardsOut(cards=[CardOut.of(card) for card in search_cards(q, limit=limit)])


class SimilarOut(BaseModel):
    card: CardOut
    similar: list[CardOut]


@router.get("/{card_id}/similar", response_model=SimilarOut)
def similar(card_id: str, limit: Annotated[int, Query(ge=1, le=20)] = 5) -> SimilarOut:
    """役割の近いカード（F-10 の代わりの候補）。"""
    base = card_view(card_id)
    if base is None:
        raise HTTPException(status_code=404, detail=f"カードが見つかりません: {card_id}")
    found = [card_view(other_id) for other_id, _ in similar_cards(card_id, limit=limit)]
    return SimilarOut(card=CardOut.of(base), similar=[CardOut.of(c) for c in found if c])
