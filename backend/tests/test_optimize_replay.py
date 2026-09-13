"""診断の 1 試合をログ付きで再現する（`optimize.replay`）のテスト。"""

import json
from pathlib import Path

from pocket_api.optimize.diagnose import diagnose_against
from pocket_api.optimize.replay import replay_diagnosis_game

SNAPSHOT = json.loads(
    Path("/workspace/data/meta/2026-09-07_B4a-standard.json").read_text(encoding="utf-8")
)
SAMPLE = Path("/workspace/data/decks/mydeck_sample003.txt").read_text(encoding="utf-8")
OPPONENT = SNAPSHOT["archetypes"][0]


def test_replays_the_same_game_as_the_diagnosis_from_my_side() -> None:
    opponents = [(OPPONENT["name"], OPPONENT["share"], OPPONENT["decklists"][0])]
    diagnosis = diagnose_against(SAMPLE, opponents, games=12)
    outcomes = diagnosis.matchups[0].outcomes
    for index in (0, 7):
        game = replay_diagnosis_game(SAMPLE, OPPONENT["decklists"][0], games=12, index=index)
        assert game.me_first is (index < 6)
        assert game.outcome == outcomes[index]
        # 自分（診断したデッキ）は、先攻でも後攻でも me の側に入る
        first = game.steps[0]
        assert first.actor == ("me" if game.me_first else "opp")
        mine = {c.name_ja for c in game.opening_me}
        assert mine, "初手がある"


def test_names_are_japanese_and_reprints_use_the_lowest_rarity_print() -> None:
    game = replay_diagnosis_game(SAMPLE, OPPONENT["decklists"][0], games=12, index=0)
    cards = [c for step in game.steps for c in step.hand]
    assert cards
    assert all(c.id != "A2b 111" for c in cards), "モンスターボールは ◆1 の印刷で出す"
    assert all(c.name_ja and not c.name_ja.isascii() for c in cards), "カード名は和名"
    texts = " ".join(step.text for step in game.steps)
    assert "ターンを終える" in texts
