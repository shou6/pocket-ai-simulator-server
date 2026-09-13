"""デッキの検証・デッキコードの読み書き・診断（F-02 / F-07 / F-08 / F-11 / F-12）。

中身は `pocket_api.cards` と `pocket_api.optimize` の関数を呼ぶだけにする。
CLI と WEB で結果が食い違わないように、ロジックをここに書かない（`docs/ui-design.md` 6 節）。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Annotated, Literal

import pocket_engine_py as engine
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pocket_api.api.deps import get_session
from pocket_api.api.meta import current_archetypes, latest_meta, meta_opponents
from pocket_api.api.schemas import ArchetypeOut, CardOut, DeckOut, Precision
from pocket_api.cards.catalog import card_view, translator
from pocket_api.cards.deck_code import decode_deck_code, encode_deck_code
from pocket_api.optimize.alternatives import alternatives
from pocket_api.optimize.archetype import MetaArchetype, classify
from pocket_api.optimize.diagnose import diagnose_against, unimplemented_cards
from pocket_api.optimize.improve_cli import delta_noise, judge_delta
from pocket_api.optimize.recipe import DeckRecipe

router = APIRouter(prefix="/decks", tags=["decks"])

STRATEGY = "l"
"""方策は選ばせない。`l` 以外は校正していない（`docs/ui-design.md` 5 節）。"""

GameOption = Literal[60, 200, 800]
"""試合数の 3 択（速い / ふつう / 正確）。"""


class DecklistIn(BaseModel):
    decklist: str


class UnimplementedOut(BaseModel):
    """評価できないカード（F-08）。どのカードかを名指しする。"""

    id: str
    name: str
    name_ja: str
    reason: str


class ValidationOut(BaseModel):
    ok: bool
    problems: list[str]
    unimplemented: list[UnimplementedOut]
    deck: DeckOut | None
    """読み取れたデッキ。行が読めなければ `None`。"""

    archetype: ArchetypeOut | None = None
    """どういうデッキか。行が読めなければ `None`。"""


def _unimplemented(decklist: str) -> list[UnimplementedOut]:
    found: list[UnimplementedOut] = []
    for card_id, name, reason in unimplemented_cards(decklist):
        view = card_view(card_id)
        found.append(
            UnimplementedOut(
                id=card_id,
                name=name,
                name_ja=view.name_ja if view else name,
                reason=reason,
            )
        )
    return found


def check_deck(decklist: str, archetypes: Sequence[MetaArchetype] = ()) -> ValidationOut:
    """デッキを検証する。20 枚・同名 2 枚以内・たね 1 枚以上・未実装カード。

    `archetypes`（環境のアーキタイプ）を渡すと、どういうデッキかも判定して添える。
    """
    try:
        recipe = DeckRecipe.from_text(decklist)
    except ValueError as error:
        return ValidationOut(ok=False, problems=[str(error)], unimplemented=[], deck=None)
    blocked = _unimplemented(decklist)
    problems: list[str] = []
    if blocked:
        problems.append("未実装のカードが含まれているので評価できません")
    else:
        try:
            engine.validate_deck(recipe.to_text())
        except ValueError as error:
            problems.append(str(error))
    return ValidationOut(
        ok=not problems,
        problems=problems,
        unimplemented=blocked,
        deck=DeckOut.from_recipe(recipe),
        archetype=ArchetypeOut.of(classify(recipe, archetypes)),
    )


@router.post("/validate", response_model=ValidationOut)
def validate(body: DecklistIn, session: Annotated[Session, Depends(get_session)]) -> ValidationOut:
    """デッキが評価できる形かを返す。問題があっても 200 で理由を返す。"""
    return check_deck(body.decklist, current_archetypes(session))


class CodeIn(BaseModel):
    code: str


class DecodeOut(BaseModel):
    deck: DeckOut
    validation: ValidationOut


@router.post("/decode", response_model=DecodeOut)
def decode(body: CodeIn, session: Annotated[Session, Depends(get_session)]) -> DecodeOut:
    """デッキコード（QR の中身）をデッキにする（F-11）。"""
    try:
        recipe = decode_deck_code(body.code)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return DecodeOut(
        deck=DeckOut.from_recipe(recipe),
        validation=check_deck(recipe.to_text(), current_archetypes(session)),
    )


class EncodeOut(BaseModel):
    code: str


@router.post("/encode", response_model=EncodeOut)
def encode(body: DecklistIn) -> EncodeOut:
    """デッキをデッキコードにする（F-12）。QR 画像にするのはブラウザ。"""
    try:
        code = encode_deck_code(DeckRecipe.from_text(body.decklist))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return EncodeOut(code=code)


class DiagnoseIn(BaseModel):
    decklist: str
    games: GameOption = 200


class MatchupOut(BaseModel):
    name: str
    name_ja: str
    share: float
    win_rate: float
    interval: tuple[float, float]
    going_first: float
    going_second: float


class DiagnosisOut(BaseModel):
    deck: DeckOut
    archetype: ArchetypeOut
    overall: float
    interval: tuple[float, float]
    going_first: float
    going_second: float
    matchups: list[MatchupOut]
    precision: Precision
    snapshot_fetched_at: str
    elapsed_seconds: float


class DeckProblem(BaseModel):
    """評価できないデッキのときの 422 の中身。"""

    message: str
    validation: ValidationOut


@router.post(
    "/diagnose",
    response_model=DiagnosisOut,
    responses={422: {"model": DeckProblem}},
)
def diagnose(body: DiagnoseIn, session: Annotated[Session, Depends(get_session)]) -> DiagnosisOut:
    """メタ環境への期待勝率（F-02 / F-07）。1 組 200 試合で約 12 秒かかる。

    ジョブにしないのは、待たせられる長さだから（`docs/ui-design.md` 1 節）。
    """
    validation = check_deck(body.decklist)
    if not validation.ok:
        raise HTTPException(
            status_code=422,
            detail=DeckProblem(
                message="このデッキは評価できません", validation=validation
            ).model_dump(),
        )
    snapshot, decks = latest_meta(session)
    started = time.perf_counter()
    result = diagnose_against(
        body.decklist, meta_opponents(decks), strategy=STRATEGY, games=body.games
    )
    names = translator()
    return DiagnosisOut(
        deck=DeckOut.from_text(body.decklist),
        archetype=ArchetypeOut.of(
            classify(DeckRecipe.from_text(body.decklist), current_archetypes(session))
        ),
        overall=result.overall,
        interval=result.overall_interval,
        going_first=result.overall_going_first,
        going_second=result.overall_going_second,
        matchups=[
            MatchupOut(
                name=m.name,
                name_ja=names.text(m.name),
                share=m.share,
                win_rate=m.win_rate,
                interval=m.interval,
                going_first=m.going_first,
                going_second=m.going_second,
            )
            for m in result.matchups
        ],
        precision=Precision(strategy=STRATEGY, games=body.games, noise=delta_noise(body.games)),
        snapshot_fetched_at=snapshot.fetched_at.isoformat(),
        elapsed_seconds=time.perf_counter() - started,
    )


class SwapIn(BaseModel):
    decklist: str
    out_card: str
    in_card: str
    games: GameOption = 200


class SwapOut(BaseModel):
    """1 枚を差し替えた場合の勝率（F-10）。元のデッキと同じ試合列で比べる。"""

    deck: DeckOut
    base_win_rate: float
    win_rate: float
    delta: float
    verdict: str
    """`clear` / `likely` / `none` / `worse`（`improve_cli.judge_delta`）。"""

    precision: Precision


@router.post("/swap", response_model=SwapOut, responses={422: {"model": DeckProblem}})
def swap(body: SwapIn, session: Annotated[Session, Depends(get_session)]) -> SwapOut:
    """持っていないカードを役割の近いカードに差し替えたら、何%変わるか。診断 2 回ぶん。"""
    try:
        recipe = DeckRecipe.from_text(body.decklist)
        swapped = recipe.with_change(body.out_card, -1).with_change(body.in_card, 1)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    validation = check_deck(swapped.to_text())
    if not validation.ok:
        raise HTTPException(
            status_code=422,
            detail=DeckProblem(
                message="差し替えたデッキは組めません", validation=validation
            ).model_dump(),
        )
    _, decks = latest_meta(session)
    opponents = meta_opponents(decks)
    base = diagnose_against(recipe.to_text(), opponents, strategy=STRATEGY, games=body.games)
    after = diagnose_against(swapped.to_text(), opponents, strategy=STRATEGY, games=body.games)
    delta = after.overall - base.overall
    return SwapOut(
        deck=DeckOut.from_recipe(swapped),
        base_win_rate=base.overall,
        win_rate=after.overall,
        delta=delta,
        verdict=judge_delta(delta, body.games),
        precision=Precision(strategy=STRATEGY, games=body.games, noise=delta_noise(body.games)),
    )


class AlternativesIn(BaseModel):
    decklist: str
    card_id: str
    limit: int = Field(default=6, ge=1, le=20)
    excluded: list[str] = Field(default_factory=list)
    """「持っていない」と言われたカード。代わりにも出さない。"""


class AlternativesOut(BaseModel):
    card: CardOut
    alternatives: list[CardOut]


@router.post("/alternatives", response_model=AlternativesOut)
def list_alternatives(body: AlternativesIn) -> AlternativesOut:
    """持っていないカードの代わり（F-10）。役割が近く、このデッキに入れて働くカードだけを返す。"""
    try:
        recipe = DeckRecipe.from_text(body.decklist)
        found = alternatives(recipe, body.card_id, limit=body.limit, excluded=body.excluded)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    base = card_view(body.card_id)
    if base is None:
        raise HTTPException(status_code=422, detail=f"カードが見つかりません: {body.card_id}")
    views = [view for view in (card_view(card_id) for card_id in found) if view is not None]
    return AlternativesOut(card=CardOut.of(base), alternatives=[CardOut.of(v) for v in views])
