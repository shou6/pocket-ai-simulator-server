"""デッキの特徴量のテスト。

サロゲートモデル（勝率を予測する代理評価器）に渡すため、デッキを数値の並びにする。
1 デッキの実評価に約 7 秒かかるのに対し、モデルなら一瞬で済む。
"""

from pathlib import Path

from pocket_api.optimize.features import FEATURE_NAMES, deck_features

SAMPLE = Path("/workspace/data/decks/mydeck_sample002.txt")


def test_features_have_a_fixed_length_and_names() -> None:
    values = deck_features(SAMPLE.read_text(encoding="utf-8"))
    assert len(values) == len(FEATURE_NAMES)
    assert all(isinstance(v, float) for v in values)


def test_features_are_deterministic() -> None:
    text = SAMPLE.read_text(encoding="utf-8")
    assert deck_features(text) == deck_features(text)


def test_features_ignore_the_order_of_lines() -> None:
    text = SAMPLE.read_text(encoding="utf-8")
    shuffled = "\n".join(reversed(text.strip().splitlines()))
    assert deck_features(text) == deck_features(shuffled)


def test_features_capture_the_deck_composition() -> None:
    named = dict(zip(FEATURE_NAMES, deck_features(SAMPLE.read_text(encoding="utf-8")), strict=True))
    # エルフバズーカはポケモン 8 枚・トレーナーズ 12 枚
    assert named["pokemon_count"] == 8.0
    assert named["trainer_count"] == 12.0
    # たね 4 枚（イトマル 2・モンメン 2）、1 進化 4 枚（アリアドス 2・エルフーン ex 2）
    assert named["basic_count"] == 4.0
    assert named["stage1_count"] == 4.0
    # エルフーン ex を 2 枚持つので、渡しうるポイントは増える
    assert named["ex_count"] == 2.0


def test_different_decks_have_different_features() -> None:
    a = deck_features(SAMPLE.read_text(encoding="utf-8"))
    other = Path("/workspace/data/decks/mydeck_sample001.txt")
    b = deck_features(other.read_text(encoding="utf-8"))
    assert a != b


def test_features_detect_a_thin_evolution_line() -> None:
    """進化元の枚数が足りるかを見る。

    ユーザーの指摘：メガルカリオデッキのリオル 2 枚は、メガルカリオ ex 2 枚と
    ルカリオ 1 枚（計 3 枚）の進化元を担っている。1 枚減れば主役が立たなくなるので、
    「たね同士の入れ替え」でも特徴量が変わらなければならない。
    """
    from pocket_api.optimize.recipe import DeckRecipe

    lucario = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")
    import json

    rows = json.loads(lucario.read_text(encoding="utf-8"))["archetypes"]
    text = next(r for r in rows if "Lucario" in r["name"])["decklists"][0]
    base = DeckRecipe.from_text(text)

    # リオル（A2 091）を 1 枚抜いてアサナン（別のたね）に替える
    thinner = base.with_change("A2 091", -1).with_change("A2 106", 1)
    before = dict(zip(FEATURE_NAMES, deck_features(base.to_text()), strict=True))
    after = dict(zip(FEATURE_NAMES, deck_features(thinner.to_text()), strict=True))

    assert before["evolution_supply"] > after["evolution_supply"], (
        "進化元が減れば、充足の度合いが下がるはず"
    )


def test_features_count_cards_that_cannot_evolve() -> None:
    """進化元が 1 枚もない進化カードは、入れても腐る。"""
    from pocket_api.optimize.recipe import DeckRecipe

    # メガルカリオ ex（B3 081、進化元リオル）だけを入れ、リオルを入れない
    orphan = DeckRecipe(
        cards=(("B3 081", 2), ("A1 155", 2), ("P-A 007", 2), ("P-A 005", 2), ("A1 154", 12)),
        energy=("Fighting",),
    )
    named = dict(zip(FEATURE_NAMES, deck_features(orphan.to_text()), strict=True))
    assert named["orphan_evolutions"] == 2.0


def test_features_classify_trainers_by_their_effect() -> None:
    """トレーナーズを効果で分ける。

    「サポート 1 枚」としか見ないと、ポケモンセンターレディ（回復）とイリダ（別効果）が
    同じデッキに見える。役割で分ければ 1 枚の入れ替えを区別できる。
    """
    from pocket_api.optimize.recipe import DeckRecipe

    def named(recipe: DeckRecipe) -> dict[str, float]:
        return dict(zip(FEATURE_NAMES, deck_features(recipe.to_text()), strict=True))

    base = DeckRecipe(
        cards=(("A1 155", 2), ("A2b 070", 2), ("P-A 007", 2), ("A1 154", 14)),
        energy=("Fighting",),
    )
    # ポケモンセンターレディ（回復）をコルニ（打点強化）に替える
    swapped = base.with_change("A2b 070", -1).with_change("B3 149", 1)
    assert named(base)["heal_count"] > named(swapped)["heal_count"]


def test_features_count_damage_boosting_cards() -> None:
    """打点を上げるカード（スタジアムやサポート）を数える。"""
    from pocket_api.optimize.recipe import DeckRecipe

    without = DeckRecipe(cards=(("A1 155", 2), ("A1 154", 18)), energy=("Fighting",))
    with_arena = without.with_change("A1 154", -1).with_change("B3 154", 1)
    a = dict(zip(FEATURE_NAMES, deck_features(without.to_text()), strict=True))
    b = dict(zip(FEATURE_NAMES, deck_features(with_arena.to_text()), strict=True))
    assert b["boost_count"] > a["boost_count"]
