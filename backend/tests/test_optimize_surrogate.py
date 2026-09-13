"""サロゲートモデルのテスト。

特徴量から期待勝率を予測する代理評価器。探索で数千の候補を見るために使う。
学習データはシミュレーションの結果なので、貯めるほど精度が上がる。
"""

import pytest

from pocket_api.optimize.surrogate import Surrogate


def test_learns_a_linear_relationship() -> None:
    # 1 つ目の特徴量に比例する的を学習できるか
    samples = [((float(i), 1.0), 0.1 * i) for i in range(10)]
    model = Surrogate.fit(samples)
    assert model.predict((3.0, 1.0)) == pytest.approx(0.3, abs=0.05)
    assert model.predict((7.0, 1.0)) == pytest.approx(0.7, abs=0.05)


def test_predicts_within_the_rate_range() -> None:
    # 勝率なので 0〜1 に収める
    samples = [((float(i), 1.0), 0.1 * i) for i in range(10)]
    model = Surrogate.fit(samples)
    assert 0.0 <= model.predict((100.0, 1.0)) <= 1.0
    assert 0.0 <= model.predict((-100.0, 1.0)) <= 1.0


def test_needs_at_least_two_samples() -> None:
    with pytest.raises(ValueError, match="学習データ"):
        Surrogate.fit([((1.0, 1.0), 0.5)])


def test_reports_its_own_accuracy() -> None:
    samples = [((float(i), 1.0), 0.1 * i) for i in range(10)]
    model = Surrogate.fit(samples)
    # 学習に使ったデータでの平均絶対誤差
    assert model.train_error < 0.05


def test_handles_constant_targets() -> None:
    # すべて同じ勝率なら、その値を返す
    samples = [((float(i), 1.0), 0.4) for i in range(5)]
    model = Surrogate.fit(samples)
    assert model.predict((2.0, 1.0)) == pytest.approx(0.4, abs=0.01)


def test_gradient_boosting_learns_a_nonlinear_relationship() -> None:
    """デッキの強さは組み合わせで決まるので、線形和では捉えきれない。

    2 つの特徴量の積で決まる的を用意する。線形モデルには学べないが、
    勾配ブースティングなら追える。
    """
    from pocket_api.optimize.surrogate import BoostedSurrogate

    # 学習率を下げてある（実データでの過学習を抑えるため）ので、
    # 木が育つだけのデータ量を用意する
    samples = [((float(a), float(b)), 0.1 * a * b / 19.0) for a in range(20) for b in range(20)]
    boosted = BoostedSurrogate.fit(samples)
    linear = Surrogate.fit(samples)
    assert boosted.train_error < linear.train_error, "非線形を扱えるぶん誤差が小さいはず"
    assert boosted.predict((19.0, 19.0)) == pytest.approx(1.0, abs=0.15)


def test_gradient_boosting_predicts_within_the_rate_range() -> None:
    from pocket_api.optimize.surrogate import BoostedSurrogate

    samples = [((float(i), 1.0), 0.1 * i) for i in range(10)]
    model = BoostedSurrogate.fit(samples)
    assert 0.0 <= model.predict((100.0, 1.0)) <= 1.0
    assert 0.0 <= model.predict((-100.0, 1.0)) <= 1.0


def test_gradient_boosting_is_deterministic() -> None:
    """同じデータなら同じモデルになる（シミュレーションの決定性と揃える）。"""
    from pocket_api.optimize.surrogate import BoostedSurrogate

    samples = [((float(i), float(i % 3)), 0.05 * i) for i in range(30)]
    first = BoostedSurrogate.fit(samples)
    second = BoostedSurrogate.fit(samples)
    assert first.predict((5.0, 2.0)) == pytest.approx(second.predict((5.0, 2.0)))
