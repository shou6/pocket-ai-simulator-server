"""Rust エンジンの Python バインディングのテスト。

ここでは対戦ロジックそのものではなく、Python から呼べること、
エラーが例外になること、決定性が保たれることを確認する。
"""

import pocket_engine_py as engine
import pytest

FIRE_DECK = """\
Energy: Fire
2 A1 042
2 A1 043
2 A1 044
2 A1 049
2 A1 050
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
"""

WATER_DECK = """\
Energy: Water
2 A1 053
2 A1 054
2 A1 055
2 A1 219
2 A1 220
2 A1 225
2 P-A 001
2 P-A 002
2 P-A 005
2 P-A 007
"""


def test_exposes_engine_version_and_deckgym_revision() -> None:
    """バージョンとリビジョンを取得できる。"""
    assert engine.engine_version() == "0.1.0"
    # 勝率キャッシュのキーに使うので 40 桁のコミットハッシュであること
    assert len(engine.deckgym_revision()) == 40


def test_validate_deck_accepts_valid_deck() -> None:
    """有効なデッキを検証できる。"""
    engine.validate_deck(FIRE_DECK)


def test_validate_deck_rejects_unknown_energy_type() -> None:
    """不正なエネルギー種別は panic せず例外になる。"""
    # Rust 側が panic せず例外に変換されること
    with pytest.raises(ValueError, match="エネルギー種別"):
        engine.validate_deck(FIRE_DECK.replace("Energy: Fire", "Energy: Flame"))


def test_validate_deck_rejects_wrong_card_count() -> None:
    """枚数が足りないデッキは例外になる。"""
    with pytest.raises(ValueError, match="20 枚"):
        engine.validate_deck(FIRE_DECK.replace("2 A1 042\n", ""))


def test_evaluate_matchup_returns_records() -> None:
    """相性を評価し、座席ごとの成績を返す。"""
    result = engine.evaluate_matchup(FIRE_DECK, WATER_DECK, "aa", "aa", 40, 1)

    assert result.overall.games == 40
    assert result.going_first.games == 20
    assert result.going_second.games == 20
    assert result.strategy_a == "aa"
    assert result.strategy_b == "aa"
    assert result.seed == 1
    assert result.deckgym_revision == engine.deckgym_revision()


def test_evaluate_matchup_returns_win_rate_and_interval() -> None:
    """勝率と信頼区間を取得できる。"""
    result = engine.evaluate_matchup(FIRE_DECK, WATER_DECK, "aa", "aa", 40, 1)

    win_rate = result.overall.win_rate
    assert win_rate is not None
    low, high = result.overall.confidence_interval_95
    assert 0.0 <= low <= win_rate <= high <= 1.0


def test_evaluate_matchup_is_deterministic() -> None:
    """同じシードなら同じ結果になる。"""
    first = engine.evaluate_matchup(FIRE_DECK, WATER_DECK, "aa", "aa", 40, 7)
    second = engine.evaluate_matchup(FIRE_DECK, WATER_DECK, "aa", "aa", 40, 7)
    assert first.overall.wins == second.overall.wins
    assert first.overall.losses == second.overall.losses
    assert first.overall.ties == second.overall.ties


def test_evaluate_matchup_rejects_unknown_strategy() -> None:
    """未知の方策コードは例外になる。"""
    with pytest.raises(ValueError, match="方策コード"):
        engine.evaluate_matchup(FIRE_DECK, WATER_DECK, "zzz", "aa", 10, 1)


def test_evaluate_matchup_rejects_zero_games() -> None:
    """試合数が零なら例外になる。"""
    with pytest.raises(ValueError, match="試合数"):
        engine.evaluate_matchup(FIRE_DECK, WATER_DECK, "aa", "aa", 0, 1)


def test_evaluate_matchup_rejects_invalid_deck() -> None:
    """不正なデッキを渡すと例外になる。"""
    with pytest.raises(ValueError, match="20 枚"):
        engine.evaluate_matchup("Energy: Fire\n2 A1 042\n", WATER_DECK, "aa", "aa", 10, 1)


def test_card_statuses_lists_every_card() -> None:
    """すべてのカードの実装状況を取得できる。"""
    statuses = engine.card_statuses_all()
    assert len(statuses) > 1000
    bulbasaur = next(card for card in statuses if card.id == "A1 001")
    assert bulbasaur.name == "Bulbasaur"
    assert bulbasaur.is_complete


def test_incomplete_card_statuses_filters_complete_cards() -> None:
    """未実装カードだけを取り出せる。理由も付く。

    カードは全て実装済みになったので、いまは空になるのが正しい
    （docs/deckgym-fork.md）。本家の新カードで未実装が生まれたらここが落ちる。
    """
    incomplete = engine.incomplete_card_statuses()
    assert [card.id for card in incomplete] == []
    assert all(not card.is_complete for card in incomplete)
    assert all(card.description for card in incomplete)


def test_normalize_deck_infers_missing_energy_line() -> None:
    """エネルギー行がないデッキリストは推定して補う。"""
    without_energy = FIRE_DECK.split("Energy: Fire\n")[1]
    normalized = engine.normalize_deck(without_energy)
    assert normalized.startswith("Energy: Fire")
    engine.validate_deck(normalized)


def test_normalize_deck_keeps_existing_energy_line() -> None:
    """既にエネルギー行があればそのまま返す。"""
    assert engine.normalize_deck(FIRE_DECK) == FIRE_DECK


def test_validate_deck_rejects_unknown_cards() -> None:
    """解釈できないデッキは例外にする。

    未実装カードを弾く経路（deckgym は未実装カードが場に出ると panic する）は
    engine 側にあるが、カードは全て実装済みになったので実在のカードでは通せない
    （docs/deckgym-fork.md）。ここでは Rust のエラーが ValueError になることを見る。
    """
    with pytest.raises(ValueError, match="解釈できません"):
        engine.validate_deck(FIRE_DECK.replace("2 A1 042", "2 A1 999"))
