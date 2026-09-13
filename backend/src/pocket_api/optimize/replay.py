"""診断した 1 試合を、ログ付きで再現して画面に出す形にする。

診断（`diagnose.diagnose_against`）は相手ごとに N 試合を回し、試合ごとの勝敗を返す。
その番号を渡すと、エンジンが同じシードと先攻で同じ試合をもう一度回し、行動ごとの盤面を記録する
（`pocket_engine_py.replay_matchup_game`）。記録は保存しない。同じ引数なら必ず同じ試合になる。

エンジンの記録はプレイヤー 0 が先攻。画面では「自分（診断したデッキ）」と「相手」で見せたいので、
ここで座席を入れ替え、カード名を和名に、再録をレアリティの低い印刷にそろえる。
開発用の HTML ビューア（`scripts/board_view.py`）と同じ情報を持つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pocket_engine_py as engine

from pocket_api.cards.catalog import card_view, translator
from pocket_api.cards.deck_builder_ids import deck_builder_ids
from pocket_api.optimize.cache import canonical_deck

Side = Literal["me", "opp"]


@dataclass(frozen=True)
class ReplayCard:
    id: str
    """画像に使う ID。再録はレアリティの低い印刷。"""

    name_ja: str


@dataclass(frozen=True)
class ReplayPokemon:
    id: str
    name_ja: str
    hp: int
    max_hp: int
    energy: tuple[str, ...]
    """付いているエネルギー（`草` など）。"""

    status: tuple[str, ...]
    tools: tuple[str, ...]
    """付いているどうぐの和名。"""


@dataclass(frozen=True)
class ReplaySide:
    points: int
    hand: int
    """手札の枚数。"""

    deck: int
    """山札の枚数。"""

    active: ReplayPokemon | None
    bench: tuple[ReplayPokemon, ...]


@dataclass(frozen=True)
class ReplayStep:
    no: int
    """1 始まりの通し番号。"""

    turn: int
    actor: Side
    text: str
    """行動の説明（和名）。"""

    me: ReplaySide
    opp: ReplaySide
    stadium: ReplayCard | None
    stadium_mine: bool
    """スタジアムを出したのが自分か。"""

    hand: tuple[ReplayCard, ...]
    """行動した側の、行動後の手札。"""

    drew: bool
    """カードを引いた行動か（画面で手札を並べて見せる）。"""


@dataclass(frozen=True)
class GameReplay:
    index: int
    me_first: bool
    outcome: str
    """自分から見た結果（`win` / `loss` / `tie` / `unfinished`）。"""

    points_me: int
    points_opp: int
    turns: int
    opening_me: tuple[ReplayCard, ...]
    opening_opp: tuple[ReplayCard, ...]
    steps: tuple[ReplayStep, ...]


def _name(card_id: str, english: str) -> str:
    view = card_view(card_id)
    return view.name_ja if view else translator().card_name(english)


def _card(ref: Any) -> ReplayCard:
    return ReplayCard(id=deck_builder_ids().canonical(ref.id), name_ja=_name(ref.id, ref.name))


def _pokemon(snapshot: Any) -> ReplayPokemon:
    return ReplayPokemon(
        id=deck_builder_ids().canonical(snapshot.id),
        name_ja=_name(snapshot.id, snapshot.name),
        hp=snapshot.remaining_hp,
        max_hp=snapshot.max_hp,
        energy=tuple(snapshot.energy),
        status=tuple(snapshot.status),
        tools=tuple(_name(tool.id, tool.name) for tool in snapshot.tools),
    )


def _side(step: Any, player: int) -> ReplaySide:
    active = step.active[player]
    return ReplaySide(
        points=step.points[player],
        hand=step.hand_size[player],
        deck=step.deck_size[player],
        active=_pokemon(active) if active is not None else None,
        bench=tuple(_pokemon(p) for p in step.bench[player]),
    )


def replay_diagnosis_game(
    decklist: str,
    opponent_decklist: str,
    *,
    strategy: str = "l",
    games: int,
    seed: int = 1,
    index: int,
) -> GameReplay:
    """診断と同じ引数で `index` 試合目（0 始まり）を再現する。範囲外なら `ValueError`。"""
    # 診断と同じく表記を揃える。行の順序はシャッフルの元になる（`cache.canonical_deck`）
    game = engine.replay_matchup_game(
        canonical_deck(decklist),
        canonical_deck(opponent_decklist),
        strategy,
        strategy,
        games,
        seed,
        index,
    )
    me = 0 if game.a_is_first else 1
    opp = 1 - me
    tr = translator()
    replay = game.replay
    steps = tuple(
        ReplayStep(
            no=number,
            turn=step.turn,
            actor="me" if step.actor == me else "opp",
            text=tr.text(step.description),
            me=_side(step, me),
            opp=_side(step, opp),
            stadium=_card(step.stadium) if step.stadium is not None else None,
            stadium_mine=step.stadium_owner == me,
            hand=tuple(_card(c) for c in step.hand[step.actor]),
            drew="枚引く" in step.description,
        )
        for number, step in enumerate(replay.steps, start=1)
    )
    return GameReplay(
        index=index,
        me_first=game.a_is_first,
        outcome=game.outcome,
        points_me=replay.points[me],
        points_opp=replay.points[opp],
        turns=replay.turns,
        opening_me=tuple(_card(c) for c in replay.opening_hands[me]),
        opening_opp=tuple(_card(c) for c in replay.opening_hands[opp]),
        steps=steps,
    )
