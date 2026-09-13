"""デッキ評価のテスト。

候補デッキをメタ環境と戦わせ、使用率で重み付けした期待勝率を返す。
弱い候補を全試合ぶん回さずに切り上げる逐次検定も持つ。
"""

import pytest

from pocket_api.optimize.cache import MatchupCache
from pocket_api.optimize.evaluate import Opponent, expected_win_rate, should_stop_early

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


def test_expected_win_rate_weights_by_usage_share() -> None:
    # 使用率の高い相手ほど、期待勝率への寄与が大きい
    opponents = [
        Opponent(name="多い", decklist=WATER_DECK, share=0.9),
        Opponent(name="少ない", decklist=FIRE_DECK, share=0.1),
    ]
    result = expected_win_rate(FIRE_DECK, opponents, strategy="p", games=40)
    # ミラー（対 FIRE）はおよそ 50%。対 WATER の勝率が支配的になる
    per_opponent = dict(result.per_opponent)
    assert set(per_opponent) == {"多い", "少ない"}
    assert result.win_rate == pytest.approx(
        0.9 * per_opponent["多い"] + 0.1 * per_opponent["少ない"], abs=1e-9
    )


def test_expected_win_rate_normalizes_shares() -> None:
    # 使用率の合計が 1 でなくても、比率として扱う
    opponents = [
        Opponent(name="a", decklist=WATER_DECK, share=2.0),
        Opponent(name="b", decklist=FIRE_DECK, share=2.0),
    ]
    result = expected_win_rate(FIRE_DECK, opponents, strategy="p", games=40)
    per = dict(result.per_opponent)
    assert result.win_rate == pytest.approx((per["a"] + per["b"]) / 2, abs=1e-9)


def test_mirror_match_is_around_half() -> None:
    opponents = [Opponent(name="自分", decklist=FIRE_DECK, share=1.0)]
    result = expected_win_rate(FIRE_DECK, opponents, strategy="p", games=200)
    assert result.win_rate == pytest.approx(0.5, abs=0.12)


def test_should_stop_early_when_clearly_worse() -> None:
    # 40 試合で 8 勝しかしていない候補は、基準 50% に届く見込みが薄い
    assert should_stop_early(wins=8, games=40, target=0.5, confidence=0.95)


def test_should_not_stop_early_when_close() -> None:
    # 互角に近いなら、まだ切り上げない
    assert not should_stop_early(wins=19, games=40, target=0.5, confidence=0.95)


def test_should_not_stop_early_with_few_games() -> None:
    # 試合数が少なすぎるうちは判断しない
    assert not should_stop_early(wins=1, games=6, target=0.5, confidence=0.95)


def test_stops_early_when_the_target_is_out_of_reach() -> None:
    """届かないと分かった候補は、残りの相手を評価しない。

    `should_stop_early` は実装されていたが、どこからも呼ばれていなかった
    （2026-09-10 のユーザー指摘で判明）。探索は候補を何百も評価するので、
    見込みのないものを早く切れば大きく短縮できる。
    """
    opponents = [Opponent(name=f"相手 {i}", decklist=WATER_DECK, share=0.1) for i in range(10)]
    cache = MatchupCache()
    # 目標を高く置けば、途中で届かないと分かる
    result = expected_win_rate(
        FIRE_DECK, opponents, strategy="p", games=20, cache=cache, target=0.95
    )
    assert result.stopped_early, "打ち切ったことが分かる"
    assert len(result.per_opponent) < len(opponents), "全部は評価しない"

    # 目標を渡さなければ従来どおり全部評価する
    full = expected_win_rate(FIRE_DECK, opponents, strategy="p", games=20, cache=cache)
    assert not full.stopped_early
    assert len(full.per_opponent) == len(opponents)
