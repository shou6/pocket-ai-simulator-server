"""DB モデルのテスト。

要件 9 章のエンティティのうち、最適化の出力形状に依存しないものを先に作る。

- `SimMatchup`：勝率キャッシュ。いまはプロセス内メモリだけなので、
  CLI を回すたびに全部計算し直している
- `MetaSnapshot` / `MetaDeck` / `MetaMatchup`：F-01（メタデッキ一覧）の土台
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pocket_api.db.models import MetaDeck, MetaMatchup, MetaSnapshot, SimMatchup

FIRE = """\
Energy: Fire
2 A1 042
2 A1 043
2 A1 044
2 A1 049
2 A1 050
2 A1 223
2 A1 225
2 A1 219
2 A1 220
2 A2 150
"""

WATER = """\
Energy: Water
2 A1 053
2 A1 054
2 A1 055
2 A1 219
2 A1 220
2 A1 223
2 A1 225
2 A2 150
2 A1 057
2 A1 058
"""


def test_sim_matchup_is_unique_per_condition(db_session: Session) -> None:
    """同じ条件（デッキ 2 つ・エンジン版・方策・試合数・シード）は 1 行だけ。"""
    row = SimMatchup(
        deck_a="aaaa1111",
        deck_b="bbbb2222",
        engine_revision="aec8e7ee",
        strategy="l",
        games=200,
        seed=1,
        wins=104.0,
    )
    db_session.add(row)
    db_session.flush()

    duplicate = SimMatchup(
        deck_a="aaaa1111",
        deck_b="bbbb2222",
        engine_revision="aec8e7ee",
        strategy="l",
        games=200,
        seed=1,
        wins=100.0,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_sim_matchup_keeps_the_win_rate(db_session: Session) -> None:
    row = SimMatchup(
        deck_a="aaaa1111",
        deck_b="bbbb2222",
        engine_revision="aec8e7ee",
        strategy="l",
        games=200,
        seed=1,
        wins=104.0,
    )
    db_session.add(row)
    db_session.flush()
    assert row.win_rate == pytest.approx(0.52), "引き分けを 0.5 勝で数えた値"


def test_meta_snapshot_holds_its_decks_and_matchups(db_session: Session) -> None:
    snapshot = MetaSnapshot(
        source_url="https://play.limitlesstcg.com/decks",
        fetched_at=datetime(2026, 9, 7, tzinfo=UTC),
        format="standard",
        set_code="B4a",
        schema_version=2,
    )
    deck = MetaDeck(
        snapshot=snapshot,
        rank=1,
        name="Mega Lucario ex Lucario",
        slug="mega-lucario-ex-b3-lucario-a2",
        count=352,
        share=0.083,
        wins=1008,
        losses=864,
        ties=71,
        win_rate=0.5188,
        decklists=["Energy: Fighting\n2 B3 041\n"],
    )
    deck.matchups.append(
        MetaMatchup(
            opponent_slug="suicune-ex-a2b-baxcalibur-a2b",
            matches=174,
            wins=82,
            losses=82,
            ties=10,
            win_rate=0.4713,
        )
    )
    db_session.add(snapshot)
    db_session.flush()

    assert snapshot.decks == [deck]
    assert deck.matchups[0].matches == 174
    assert deck.decklists[0].startswith("Energy:")


def test_meta_deck_slug_is_unique_within_a_snapshot(db_session: Session) -> None:
    snapshot = MetaSnapshot(
        source_url="https://play.limitlesstcg.com/decks",
        fetched_at=datetime(2026, 9, 7, tzinfo=UTC),
        format="standard",
        set_code="B4a",
        schema_version=2,
    )
    for _ in range(2):
        snapshot.decks.append(
            MetaDeck(
                rank=1,
                name="Mega Lucario ex Lucario",
                slug="mega-lucario-ex-b3-lucario-a2",
                count=352,
                share=0.083,
                wins=1,
                losses=1,
                ties=0,
                win_rate=0.5,
                decklists=[],
            )
        )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_matchup_store_round_trips_a_result(db_session: Session) -> None:
    """勝率キャッシュを DB に永続化する。プロセスをまたいで再利用できる。"""
    from pocket_api.db.matchup_store import DbMatchupStore
    from pocket_api.optimize.cache import matchup_key

    store = DbMatchupStore(db_session)
    entry = matchup_key(FIRE, WATER, "l", 200, seed=1)
    assert store.get(entry) is None, "まだ測っていない"

    store.put(entry, 0.52)
    db_session.flush()
    assert store.get(entry) == pytest.approx(0.52)


def test_matchup_store_separates_conditions(db_session: Session) -> None:
    from pocket_api.db.matchup_store import DbMatchupStore
    from pocket_api.optimize.cache import matchup_key

    store = DbMatchupStore(db_session)
    store.put(matchup_key(FIRE, WATER, "l", 200, seed=1), 0.52)
    db_session.flush()
    assert store.get(matchup_key(FIRE, WATER, "l", 200, seed=2)) is None, "シードが違えば別"
    assert store.get(matchup_key(FIRE, WATER, "p", 200, seed=1)) is None, "方策が違えば別"
    assert store.get(matchup_key(FIRE, WATER, "l", 400, seed=1)) is None, "試合数が違えば別"


def test_matchup_store_answers_for_the_reversed_pair(db_session: Session) -> None:
    """A 対 B を測れば B 対 A も反転して返せる。"""
    from pocket_api.db.matchup_store import DbMatchupStore
    from pocket_api.optimize.cache import matchup_key

    store = DbMatchupStore(db_session)
    store.put(matchup_key(FIRE, WATER, "l", 200, seed=1), 0.52)
    db_session.flush()
    reversed_entry = matchup_key(WATER, FIRE, "l", 200, seed=1)
    assert store.get(reversed_entry) == pytest.approx(0.52), "鍵の向きに正規化された値"


def test_cache_uses_the_store_before_simulating(db_session: Session) -> None:
    """DB に答えがあればシミュレートしない。

    ストアが扱うのは鍵の向き（`deck_a` < `deck_b`）に正規化した勝率。
    呼び出し側の向きへの反転は `MatchupCache` が行う。
    """
    from pocket_api.db.matchup_store import DbMatchupStore
    from pocket_api.optimize.cache import MatchupCache, matchup_key

    entry = matchup_key(FIRE, WATER, "p", 40, seed=1)
    store = DbMatchupStore(db_session)
    store.put(entry, 0.75)
    db_session.flush()

    cache = MatchupCache(store=store)
    expected = 1.0 - 0.75 if entry.swapped else 0.75
    assert cache.win_rate(FIRE, WATER, "p", 40) == pytest.approx(expected)
    assert cache.misses == 0, "シミュレートしていない"


def test_cache_writes_new_results_into_the_store(db_session: Session) -> None:
    """一度測れば、別のキャッシュ（＝別プロセス相当）でも測り直さずに済む。"""
    from pocket_api.db.matchup_store import DbMatchupStore
    from pocket_api.optimize.cache import MatchupCache

    store = DbMatchupStore(db_session)
    first = MatchupCache(store=store)
    rate = first.win_rate(FIRE, WATER, "p", 40)
    db_session.flush()
    assert first.misses == 1, "1 回目は実際に回す"

    second = MatchupCache(store=store)
    assert second.win_rate(FIRE, WATER, "p", 40) == pytest.approx(rate)
    assert second.misses == 0, "2 回目は DB から返す"
