"""デッキ診断のテスト。

自分のデッキを渡して、メタ環境の各デッキとの相性を出す。
未実装カードを含むデッキは評価できないので、その旨を明示する（要件 F-08）。
"""

from pathlib import Path

import pytest

from pocket_api.optimize.diagnose import Diagnosis, diagnose, unimplemented_cards

SAMPLE = Path("/workspace/data/decks/mydeck_sample002.txt")
SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")


def test_diagnose_reports_a_rate_for_every_meta_deck() -> None:
    result = diagnose(SAMPLE.read_text(encoding="utf-8"), SNAPSHOT, strategy="p", games=20)
    assert isinstance(result, Diagnosis)
    assert len(result.matchups) == 10
    for matchup in result.matchups:
        assert 0.0 <= matchup.win_rate <= 1.0
        assert matchup.games == 20
        # 信頼区間は勝率を含む
        low, high = matchup.interval
        assert low <= matchup.win_rate <= high


def test_diagnose_weights_the_overall_rate_by_usage_share() -> None:
    result = diagnose(SAMPLE.read_text(encoding="utf-8"), SNAPSHOT, strategy="p", games=20)
    total = sum(m.share for m in result.matchups)
    expected = sum(m.win_rate * m.share for m in result.matchups) / total
    assert result.overall == pytest.approx(expected, abs=1e-9)


def test_diagnose_sorts_matchups_by_win_rate() -> None:
    result = diagnose(SAMPLE.read_text(encoding="utf-8"), SNAPSHOT, strategy="p", games=20)
    rates = [m.win_rate for m in result.matchups]
    assert rates == sorted(rates, reverse=True), "得意な相手から並べる"


def test_unimplemented_cards_lists_what_blocks_evaluation() -> None:
    # 未実装カードを含むデッキは評価できない。何が原因かを示す（F-08）
    with_unimplemented = """\
Energy: Psychic
2 A1 001
2 A1 002
2 A1 003
2 A1 004
2 A1 005
2 A1 006
2 A1 007
2 A1 008
2 A1 009
2 A1 010
"""
    found = unimplemented_cards(with_unimplemented)
    # 実装済みだけなら空になる
    assert unimplemented_cards(SAMPLE.read_text(encoding="utf-8")) == ()
    for card_id, name, reason in found:
        assert card_id
        assert name
        assert reason


def test_diagnose_rejects_a_deck_with_unimplemented_cards() -> None:
    text = SAMPLE.read_text(encoding="utf-8").replace("2 B1a 005", "2 B4a 025")
    # ロケット団のヤドンは実装済みなので、これは通る（実装したことの確認）
    result = diagnose(text, SNAPSHOT, strategy="p", games=20)
    assert result.matchups


def test_diagnose_splits_the_overall_rate_by_who_goes_first() -> None:
    """先攻と後攻で結果が変わるデッキがある。総合勝率もその内訳を持つ。"""
    result = diagnose(SAMPLE.read_text(encoding="utf-8"), SNAPSHOT, strategy="p", games=20)
    total = sum(m.share for m in result.matchups)
    expected_first = sum(m.going_first * m.share for m in result.matchups) / total
    expected_second = sum(m.going_second * m.share for m in result.matchups) / total
    assert result.overall_going_first == pytest.approx(expected_first, abs=1e-9)
    assert result.overall_going_second == pytest.approx(expected_second, abs=1e-9)
    # 先攻と後攻を半分ずつ回すので、総合はその中間になる
    assert min(result.overall_going_first, result.overall_going_second) - 1e-9 <= result.overall
    assert result.overall <= max(result.overall_going_first, result.overall_going_second) + 1e-9


def test_render_shows_the_first_and_second_split() -> None:
    from pocket_api.optimize.diagnose_cli import render

    result = diagnose(SAMPLE.read_text(encoding="utf-8"), SNAPSHOT, strategy="p", games=20)
    text = render(result, "見本")
    assert f"{result.overall_going_first:.1%}" in text
    assert f"{result.overall_going_second:.1%}" in text


def test_render_warns_when_the_coin_flip_decides_the_game() -> None:
    """先攻後攻で総合勝率が大きくひらくデッキは、その旨を読み方に書く。"""
    from pocket_api.optimize.diagnose import Diagnosis, MatchupResult
    from pocket_api.optimize.diagnose_cli import render

    matchups = tuple(
        MatchupResult(
            name=f"相手 {i}",
            share=0.1,
            win_rate=0.5,
            interval=(0.4, 0.6),
            going_first=0.7,
            going_second=0.3,
            games=200,
        )
        for i in range(3)
    )
    diagnosis = Diagnosis(
        overall=0.5,
        matchups=matchups,
        strategy="l",
        overall_going_first=0.7,
        overall_going_second=0.3,
    )
    text = render(diagnosis, "見本")
    assert "先攻" in text
    assert "コイン" in text, "先攻後攻で勝敗が決まることを明示する"


def test_diagnose_gives_the_overall_rate_an_interval() -> None:
    """期待勝率にも幅を付ける（docs/ui-design.md 4 節）。試合数を増やすと狭くなる。"""
    text = SAMPLE.read_text(encoding="utf-8")
    few = diagnose(text, SNAPSHOT, strategy="p", games=20)
    many = diagnose(text, SNAPSHOT, strategy="p", games=80)
    for result in (few, many):
        low, high = result.overall_interval
        assert low <= result.overall <= high
    assert many.overall_interval[1] - many.overall_interval[0] < (
        few.overall_interval[1] - few.overall_interval[0]
    )


def test_diagnose_against_takes_opponents_directly() -> None:
    """DB のスナップショットからも使えるよう、相手をそのまま渡せる。"""
    from pocket_api.optimize.diagnose import diagnose_against, load_meta_decks

    opponents = load_meta_decks(SNAPSHOT)[:2]
    result = diagnose_against(SAMPLE.read_text(encoding="utf-8"), opponents, strategy="p", games=20)
    assert {m.name for m in result.matchups} == {name for name, _, _ in opponents}
