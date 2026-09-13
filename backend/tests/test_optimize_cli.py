"""最適化 CLI の組み立て部分のテスト。

シミュレーションは重いので、ここでは「メタの読み込み」「提案モードの選択」
「結果の整形」といった、対戦を伴わない部分を確かめる。
"""

import json
from pathlib import Path

import pytest

from pocket_api.optimize.cli import (
    build_opponents,
    independent_evaluation,
    load_meta,
    render_result,
)
from pocket_api.optimize.evaluate import Evaluation, Opponent
from pocket_api.optimize.recipe import DeckRecipe

SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")


def test_load_meta_reads_archetypes() -> None:
    decks = load_meta(SNAPSHOT)
    assert len(decks) == 10
    first = decks[0]
    assert first.name
    assert first.share > 0
    assert "Energy:" in first.decklist


def test_build_opponents_uses_usage_share() -> None:
    decks = load_meta(SNAPSHOT)
    opponents = build_opponents(decks)
    assert len(opponents) == 10
    assert sum(o.share for o in opponents) == pytest.approx(sum(d.share for d in decks))


def test_build_opponents_can_target_one_deck() -> None:
    decks = load_meta(SNAPSHOT)
    target = decks[0].name
    opponents = build_opponents(decks, target=target)
    assert [o.name for o in opponents] == [target]
    assert opponents[0].share == pytest.approx(1.0)


def test_build_opponents_rejects_unknown_target() -> None:
    decks = load_meta(SNAPSHOT)
    with pytest.raises(ValueError, match="見つかりません"):
        build_opponents(decks, target="存在しないデッキ")


def test_render_result_shows_decklist_and_rates() -> None:
    decks = load_meta(SNAPSHOT)
    recipe = DeckRecipe.from_text(decks[0].decklist)
    evaluation = Evaluation(
        win_rate=0.55,
        per_opponent=(("相手 A", 0.6), ("相手 B", 0.5)),
        games=100,
    )
    text = render_result(recipe, evaluation, decks)
    assert "55.0%" in text
    assert "相手 A" in text
    assert "Energy:" in text


def test_independent_evaluation_uses_a_different_seed() -> None:
    """成績の報告は、探索が使ったのと別の試合で測り直す。

    探索は何百もの候補から「その試合数で最も高く出たもの」を選ぶので、運で高く
    出たデッキが選ばれる。その値をそのまま成績にすると実力より高く出る。
    実測では 60 試合の報告値が 200 試合の実力より 4〜9 点高かった
    （`docs/analysis/card-pool-expansion.md`）。
    """
    calls: list[tuple[int, int]] = []

    def fake_rate(
        decklist: str,
        opponents: list[Opponent],
        *,
        strategy: str,
        games: int,
        cache: object | None = None,
        seed: int = 1,
    ) -> Evaluation:
        calls.append((seed, games))
        return Evaluation(win_rate=0.5, per_opponent=(("相手 A", 0.5),), games=games)

    decks = load_meta(SNAPSHOT)
    recipe = DeckRecipe.from_text(decks[0].decklist)
    opponents = build_opponents(decks)
    independent_evaluation(
        [recipe],
        opponents,
        strategy="l",
        games=200,
        search_seed=1,
        rate=fake_rate,
    )
    assert calls, "測り直していない"
    seed, games = calls[0]
    assert seed != 1, "探索と同じシードでは、同じ試合をなぞるだけで偏りが消えない"
    assert games == 200


def test_meta_snapshot_is_valid_json() -> None:
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert data["format"]


def test_render_result_lists_runner_up_candidates() -> None:
    """F-03: 候補上位 N 件を勝率順に、それぞれのデッキリストつきで出す。"""
    from pocket_api.optimize.cli import render_candidates

    decks = load_meta(SNAPSHOT)
    others = [
        (DeckRecipe.from_text(decks[1].decklist), 0.52),
        (DeckRecipe.from_text(decks[2].decklist), 0.48),
    ]
    text = render_candidates(others)
    assert "52.0%" in text
    assert "48.0%" in text
    assert text.count("```text") == 2, "候補ごとにデッキリストを添える"


def test_render_candidates_is_empty_when_there_is_only_one() -> None:
    from pocket_api.optimize.cli import render_candidates

    assert render_candidates([]) == ""
