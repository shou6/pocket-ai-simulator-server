"""役割の似たカードを探すテスト。

探索で 1,000 種からランダムに選んでも意味のある札は引けない。
抜くカードと役割が近いものに絞ることで、試行を無駄にしない。
代替カードのサジェスト（持っていないカードの言い換え）にも同じ仕組みを使う。
"""

from pocket_api.optimize.similar import card_profile, similar_cards


def test_profile_captures_the_role_of_a_pokemon() -> None:
    profile = card_profile("B3 081")  # メガルカリオ ex
    assert profile is not None
    assert profile.kind == "ポケモン"
    assert profile.energy_type == "闘"
    assert profile.stage == 1
    assert profile.hp == 190
    assert profile.max_damage == 90
    assert profile.min_cost == 2


def test_similar_cards_finds_a_replacement_of_the_same_role() -> None:
    # メガルカリオ ex（闘・1 進化・HP190・打点 90・コスト 2）に近い札
    found = similar_cards("B3 081", limit=5)
    names = [name for _, name in found]
    assert "Mega Lopunny ex" in names, f"同じ役割の札が挙がるはず: {names}"
    # 自分自身は返さない
    assert all(card_id != "B3 081" for card_id, _ in found)


def test_similar_cards_keeps_the_energy_type() -> None:
    for card_id, _ in similar_cards("B3 081", limit=8):
        profile = card_profile(card_id)
        assert profile is not None
        assert profile.energy_type in ("闘", "無"), "タイプが違うと動かない"


def test_similar_cards_for_a_trainer_matches_by_effect() -> None:
    # トレーナーズは効果文で似たものを探す
    found = similar_cards("P-A 007", limit=5)  # 博士の研究
    assert found, "トレーナーズにも候補が出るはず"
    for card_id, _ in found:
        profile = card_profile(card_id)
        assert profile is not None
        assert profile.kind != "ポケモン"


def test_similar_cards_can_be_limited_to_a_pool() -> None:
    pool = ("A2 091", "P-A 007")
    found = similar_cards("B3 081", limit=5, pool=pool)
    assert {card_id for card_id, _ in found} <= set(pool)
