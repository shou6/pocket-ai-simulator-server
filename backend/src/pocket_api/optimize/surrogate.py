"""サロゲートモデル（勝率を予測する代理評価器）。

デッキの特徴量から期待勝率を予測する。1 デッキの実評価に約 7 秒かかるのに対し、
モデルなら一瞬で済むので、探索で数千の候補を見られるようになる。

**モデルの予測は当たりを付けるためのもの**で、最終的な提案は必ずシミュレーションで
裏を取る。そうしないと「モデルが高く評価するが実際は弱いデッキ」を掴む。

まずは外部ライブラリなしのリッジ回帰で始める。これで精度が足りなければ、
勾配ブースティングなどの導入を検討する（そのときは ADR に残す）。
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_SAMPLES = 2
"""学習に要る最低のデータ数。"""

RIDGE_PENALTY = 1e-3
"""正則化の強さ。特徴量が共線でも解けるようにする。"""


@dataclass(frozen=True)
class Surrogate:
    """特徴量から勝率を予測するモデル。"""

    weights: tuple[float, ...]
    """各特徴量の係数。"""

    bias: float
    """切片。"""

    train_error: float
    """学習に使ったデータでの平均絶対誤差。過信しないための目安。"""

    samples: int
    """学習に使ったデータ数。"""

    def predict(self, features: tuple[float, ...]) -> float:
        """期待勝率の予測。勝率なので 0〜1 に収める。"""
        raw = self.bias + sum(w * x for w, x in zip(self.weights, features, strict=False))
        return min(1.0, max(0.0, raw))

    @classmethod
    def fit(cls, samples: list[tuple[tuple[float, ...], float]]) -> Surrogate:
        """（特徴量, 勝率）の組から学習する。"""
        if len(samples) < MIN_SAMPLES:
            raise ValueError(
                f"学習データが足りません（{len(samples)} 件、{MIN_SAMPLES} 件以上要る）"
            )
        width = len(samples[0][0])
        rows = [[*features, 1.0] for features, _ in samples]
        targets = [target for _, target in samples]

        # 正規方程式 (X^T X + λI) w = X^T y を、ガウスの消去法で解く
        size = width + 1
        matrix = [[0.0] * (size + 1) for _ in range(size)]
        for i in range(size):
            for j in range(size):
                matrix[i][j] = sum(row[i] * row[j] for row in rows)
            matrix[i][i] += RIDGE_PENALTY
            matrix[i][size] = sum(row[i] * y for row, y in zip(rows, targets, strict=True))

        solution = _solve(matrix, size)
        weights = tuple(solution[:width])
        bias = solution[width]
        predictions = [
            bias + sum(w * x for w, x in zip(weights, features, strict=False))
            for features, _ in samples
        ]
        error = sum(abs(p - y) for p, y in zip(predictions, targets, strict=True)) / len(samples)
        return cls(weights=weights, bias=bias, train_error=error, samples=len(samples))


def _solve(matrix: list[list[float]], size: int) -> list[float]:
    """拡大係数行列をガウスの消去法（部分ピボット選択つき）で解く。"""
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(matrix[r][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            continue
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        head = matrix[column][column]
        for j in range(column, size + 1):
            matrix[column][j] /= head
        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column]
            if factor == 0.0:
                continue
            for j in range(column, size + 1):
                matrix[row][j] -= factor * matrix[column][j]
    return [matrix[i][size] for i in range(size)]


@dataclass(frozen=True)
class BoostedSurrogate:
    """勾配ブースティングで勝率を予測するモデル。

    デッキの強さは「カードの組み合わせ」で決まる非線形な性質があり、線形和
    （[`Surrogate`]）では捉えきれなかった（学習データ 240 件で予測誤差 11.5%、
    常に平均値を答える場合が 12.8%）。木の集まりなら特徴量どうしの相互作用を扱える。

    乱数はシードで固定し、同じデータなら同じモデルになるようにする
    （シミュレーションの決定性と揃える）。
    """

    model: object
    """学習済みの `sklearn` 推定器。"""

    train_error: float
    """学習に使ったデータでの平均絶対誤差。過信しないための目安。"""

    samples: int
    """学習に使ったデータ数。"""

    def predict(self, features: tuple[float, ...]) -> float:
        """期待勝率の予測。勝率なので 0〜1 に収める。"""
        import numpy as np

        raw = float(self.model.predict(np.array([features]))[0])  # type: ignore[attr-defined]
        return min(1.0, max(0.0, raw))

    @classmethod
    def fit(
        cls,
        samples: list[tuple[tuple[float, ...], float]],
        *,
        seed: int = 1,
    ) -> BoostedSurrogate:
        """（特徴量, 勝率）の組から学習する。"""
        if len(samples) < MIN_SAMPLES:
            raise ValueError(
                f"学習データが足りません（{len(samples)} 件、{MIN_SAMPLES} 件以上要る）"
            )
        import numpy as np
        from sklearn.ensemble import HistGradientBoostingRegressor

        features = np.array([f for f, _ in samples])
        targets = np.array([y for _, y in samples])
        # 学習データが数百件の規模なので、既定のままだと過学習する
        # （学習時の誤差 0.033 に対し、未知データでは 0.115 だった）。
        # 学習率を下げ、葉を大きめにし、正則化を入れて抑える
        model = HistGradientBoostingRegressor(
            max_iter=200,
            min_samples_leaf=15,
            learning_rate=0.03,
            l2_regularization=1.0,
            random_state=seed,
        )
        model.fit(features, targets)
        predictions = model.predict(features)
        error = float(np.mean(np.abs(predictions - targets)))
        return cls(model=model, train_error=error, samples=len(samples))
