"""サロゲートモデルの学習データを貯める仕組みのテスト。

探索を回すほど貯まり、使うほど精度が上がる。1 行 1 サンプルの JSONL で持ち、
壊れた行があっても読み飛ばす。
"""

from dataclasses import replace
from pathlib import Path

import pytest

from pocket_api.optimize.dataset import Sample, append_samples, load_samples

FIRE = """\
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


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    append_samples(path, [Sample.of(FIRE, 0.42, strategy="p", games=40)])
    loaded = load_samples(path)
    assert len(loaded) == 1
    assert loaded[0].win_rate == pytest.approx(0.42)
    assert loaded[0].strategy == "p"
    assert len(loaded[0].features) > 0


def test_appends_without_losing_earlier_rows(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    append_samples(path, [Sample.of(FIRE, 0.4, strategy="p", games=40)])
    append_samples(path, [Sample.of(FIRE, 0.6, strategy="l", games=40)])
    assert len(load_samples(path)) == 2


def test_skips_the_same_deck_and_condition(tmp_path: Path) -> None:
    # 同じデッキを同じ条件で測り直しても、二重には貯めない
    path = tmp_path / "samples.jsonl"
    append_samples(path, [Sample.of(FIRE, 0.4, strategy="p", games=40)])
    written = append_samples(path, [Sample.of(FIRE, 0.5, strategy="p", games=40)])
    assert written == 0
    assert len(load_samples(path)) == 1


def test_ignores_broken_lines(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    append_samples(path, [Sample.of(FIRE, 0.4, strategy="p", games=40)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("これは JSON ではない\n")
    assert len(load_samples(path)) == 1


def test_load_returns_empty_for_a_missing_file(tmp_path: Path) -> None:
    assert load_samples(tmp_path / "ない.jsonl") == ()


def test_filters_by_strategy_and_games(tmp_path: Path) -> None:
    # 方策や試合数が違うデータは、混ぜると意味が変わる
    path = tmp_path / "samples.jsonl"
    append_samples(
        path,
        [
            Sample.of(FIRE, 0.4, strategy="p", games=40),
            Sample.of(FIRE, 0.6, strategy="l", games=40),
        ],
    )
    only_l = load_samples(path, strategy="l")
    assert len(only_l) == 1
    assert only_l[0].strategy == "l"


def test_keeps_the_decklist_so_features_can_be_rebuilt(tmp_path: Path) -> None:
    """デッキ本体を残す。

    特徴量の設計を変えたとき、勝率は測り直さずに特徴量だけ作り直せるようにする
    （実測に 1 デッキ 7 秒かかるので、貯めた勝率は資産になる）。
    """
    path = tmp_path / "samples.jsonl"
    append_samples(path, [Sample.of(FIRE, 0.42, strategy="p", games=40)])
    loaded = load_samples(path)[0]
    assert loaded.decklist, "デッキリストを残す"
    # 残したデッキから特徴量を作り直せる
    from pocket_api.optimize.features import deck_features

    assert deck_features(loaded.decklist) == loaded.features


def test_rebuild_features_updates_old_rows(tmp_path: Path) -> None:
    """古い行の特徴量を、今の設計で作り直せる。"""
    from pocket_api.optimize.dataset import rebuild_features

    path = tmp_path / "samples.jsonl"
    append_samples(path, [Sample.of(FIRE, 0.42, strategy="p", games=40)])
    # 特徴量が古い（短い）行に差し替える
    rows = path.read_text(encoding="utf-8").splitlines()
    import json as _json

    row = _json.loads(rows[0])
    row["features"] = [0.0, 0.0]
    path.write_text(_json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    updated = rebuild_features(path)
    assert updated == 1
    loaded = load_samples(path)[0]
    from pocket_api.optimize.features import deck_features

    assert loaded.features == deck_features(FIRE)


def test_ignores_samples_from_another_engine_by_default(tmp_path: Path) -> None:
    """エンジンが違うデータは既定で読まない。

    ルールや方策が変われば勝率のラベルは無効になる。これまでは方策と試合数でしか
    絞っておらず、**古いデータが黙って使われていた**（2026-09-10 のユーザー指摘）。
    実際、サロゲートモデルの学習データ 400 件はすべて 2 世代前のエンジンのものだった。
    """
    store = tmp_path / "samples.jsonl"
    fresh = Sample.of(FIRE, 0.5, strategy="l", games=40)
    stale = replace(fresh, engine_revision="00000000", fingerprint="stale")
    append_samples(store, [fresh, stale])

    loaded = load_samples(store, strategy="l", games=40)
    assert [s.fingerprint for s in loaded] == [fresh.fingerprint], "今のエンジンのものだけ"

    everything = load_samples(store, strategy="l", games=40, any_engine=True)
    assert len(everything) == 2, "明示すれば古いものも読める"
