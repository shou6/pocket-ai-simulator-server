"""勝率キャッシュのテスト。

同じ条件の対戦を二度シミュレートしないための仕組み。
デッキの順序（A 対 B と B 対 A）を正規化し、エンジン版と方策を含めた鍵で引く。
"""

import pocket_engine_py as engine
import pytest

from pocket_api.optimize.cache import MatchupCache, matchup_key

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
2 A2 150
2 A1 220
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
"""


def test_key_is_independent_of_deck_order() -> None:
    # 同じ 2 デッキなら、どちらを先に渡しても同じ鍵になる。
    # A 対 B を測ったら B 対 A も分かるので、対戦の回数を半分にできる
    forward = matchup_key(FIRE_DECK, WATER_DECK, "l", 100)
    backward = matchup_key(WATER_DECK, FIRE_DECK, "l", 100)
    assert forward.key == backward.key
    # 入れ替えたかどうかは記録する。勝率を読むときに反転が要る
    assert forward.swapped != backward.swapped


def test_key_includes_strategy_games_and_engine() -> None:
    base = matchup_key(FIRE_DECK, WATER_DECK, "l", 100).key
    assert matchup_key(FIRE_DECK, WATER_DECK, "p", 100).key != base
    assert matchup_key(FIRE_DECK, WATER_DECK, "l", 200).key != base
    assert engine.deckgym_revision()[:8] in base


def test_same_matchup_is_simulated_once() -> None:
    cache = MatchupCache()
    first = cache.win_rate(FIRE_DECK, WATER_DECK, "p", 40)
    assert cache.misses == 1
    second = cache.win_rate(FIRE_DECK, WATER_DECK, "p", 40)
    assert cache.misses == 1, "二度目はキャッシュから返すはず"
    assert first == pytest.approx(second)


def test_reversed_matchup_returns_inverted_rate() -> None:
    cache = MatchupCache()
    forward = cache.win_rate(FIRE_DECK, WATER_DECK, "p", 40)
    backward = cache.win_rate(WATER_DECK, FIRE_DECK, "p", 40)
    assert cache.misses == 1, "逆向きもキャッシュで返すはず"
    assert forward + backward == pytest.approx(1.0)


def test_key_ignores_decklist_formatting() -> None:
    # 行の順序や空行が違うだけの同じデッキは、同じ鍵にする
    shuffled = "\n".join(reversed(FIRE_DECK.strip().splitlines()))
    assert (
        matchup_key(FIRE_DECK, WATER_DECK, "l", 100).key
        == matchup_key(shuffled, WATER_DECK, "l", 100).key
    )


def test_key_separates_seeds() -> None:
    """シードが違えば別の試合になる。同じ鍵に入れてはいけない。"""
    a = matchup_key(FIRE_DECK, WATER_DECK, "p", 10, seed=1)
    b = matchup_key(FIRE_DECK, WATER_DECK, "p", 10, seed=2)
    assert a.key != b.key
    assert a.swapped == b.swapped, "向きの判定はシードに依らない"


def test_cache_reruns_for_a_different_seed() -> None:
    cache = MatchupCache()
    cache.win_rate(FIRE_DECK, WATER_DECK, "p", 10, seed=1)
    cache.win_rate(FIRE_DECK, WATER_DECK, "p", 10, seed=2)
    assert cache.misses == 2, "シードが違えば測り直す"
    cache.win_rate(FIRE_DECK, WATER_DECK, "p", 10, seed=1)
    assert cache.hits == 1


def test_same_deck_written_differently_gives_the_same_rate() -> None:
    """行の順序を変えただけのデッキは、同じ勝率を返さなければならない。

    行の順序はシャッフルの元になるので、そのまま渡すと別の試合列になる
    （同じデッキ・同じ相手・同じシードで 22.5% と 7.5% に割れた）。
    鍵は順序を無視するので、鍵が同じなら回す試合も同じでなければ辻褄が合わない。
    """
    energy, *cards = FIRE_DECK.strip().splitlines()
    shuffled = "\n".join([energy, *reversed(cards)])
    # キャッシュを分けて、実際に 2 回シミュレートさせる
    first = MatchupCache().win_rate(FIRE_DECK, WATER_DECK, "p", 40)
    second = MatchupCache().win_rate(shuffled, WATER_DECK, "p", 40)
    assert first == second

    shared = MatchupCache()
    shared.win_rate(FIRE_DECK, WATER_DECK, "p", 40)
    shared.win_rate(shuffled, WATER_DECK, "p", 40)
    assert shared.misses == 1, "同じデッキなので二度は回さない"


def test_canonical_deck_sorts_the_card_lines() -> None:
    from pocket_api.optimize.cache import canonical_deck

    energy, *cards = FIRE_DECK.strip().splitlines()
    shuffled = "\n".join([energy, *reversed(cards)])
    assert canonical_deck(FIRE_DECK) == canonical_deck(shuffled)
    assert canonical_deck(FIRE_DECK).startswith("Energy:"), "エネルギー行は先頭に残す"
