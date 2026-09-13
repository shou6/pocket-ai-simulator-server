"""サロゲートモデルの学習データを貯める。

探索を回すほど貯まり、使うほど精度が上がる（`docs/adr/0004-surrogate-model.md`）。
1 行 1 サンプルの JSONL で持つ。追記だけで済み、壊れた行があっても読み飛ばせる。

DB（要件 9 章の `SimMatchup`）は「対戦単位のキャッシュ」で別物。こちらはデッキ単位の
評価結果で、増え続ける再生成可能な中間データなので、まずファイルで持つ。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pocket_engine_py as engine

from pocket_api.optimize.cache import deck_fingerprint
from pocket_api.optimize.features import deck_features


@dataclass(frozen=True)
class Sample:
    """1 デッキぶんの評価結果。"""

    fingerprint: str
    """デッキの識別子。書き方の違いは吸収される。"""

    features: tuple[float, ...]
    """`features.deck_features` の並び。"""

    win_rate: float
    """メタ環境への期待勝率。"""

    strategy: str
    """評価に使った方策。"""

    games: int
    """1 組あたりの試合数。"""

    engine_revision: str
    """エンジンの版。ルールが変われば古いデータは使えない。"""

    decklist: str = ""
    """デッキ本体。特徴量の設計を変えたとき、勝率を測り直さずに作り直せるようにする。"""

    @classmethod
    def of(cls, decklist: str, win_rate: float, *, strategy: str, games: int) -> Sample:
        """デッキと勝率からサンプルを作る。"""
        return cls(
            fingerprint=deck_fingerprint(decklist),
            features=deck_features(decklist),
            win_rate=win_rate,
            strategy=strategy,
            games=games,
            engine_revision=engine.deckgym_revision()[:8],
            decklist=decklist,
        )

    def key(self) -> tuple[str, str, int, str]:
        """同じ条件で測ったかどうかを見る鍵。"""
        return (self.fingerprint, self.strategy, self.games, self.engine_revision)

    def to_json(self) -> str:
        return json.dumps(
            {
                "fingerprint": self.fingerprint,
                "features": list(self.features),
                "win_rate": self.win_rate,
                "strategy": self.strategy,
                "games": self.games,
                "engine_revision": self.engine_revision,
                "decklist": self.decklist,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> Sample | None:
        """1 行を読む。読めなければ `None`。"""
        try:
            row = json.loads(text)
            return cls(
                fingerprint=str(row["fingerprint"]),
                features=tuple(float(v) for v in row["features"]),
                win_rate=float(row["win_rate"]),
                strategy=str(row["strategy"]),
                games=int(row["games"]),
                engine_revision=str(row["engine_revision"]),
                decklist=str(row.get("decklist", "")),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


def load_samples(
    path: Path,
    *,
    strategy: str | None = None,
    games: int | None = None,
    any_engine: bool = False,
) -> tuple[Sample, ...]:
    """貯めたデータを読む。壊れた行は飛ばす。

    方策や試合数が違うデータを混ぜると意味が変わるので、絞り込めるようにしてある。

    **エンジンが違うデータは既定で読まない。** ルールや方策が変われば勝率のラベルは
    無効になる。以前は方策と試合数でしか絞っておらず、古いデータが黙って使われていた
    （実際、学習データ 400 件はすべて 2 世代前のエンジンのものだった）。
    古いものも含めたいときだけ `any_engine=True` を渡す。
    """
    current = engine.deckgym_revision()[:8]
    if not path.exists():
        return ()
    found: list[Sample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = Sample.from_json(line)
        if sample is None:
            continue
        if strategy is not None and sample.strategy != strategy:
            continue
        if games is not None and sample.games != games:
            continue
        if not any_engine and sample.engine_revision != current:
            continue
        found.append(sample)
    return tuple(found)


def rebuild_features(path: Path) -> int:
    """貯めたデータの特徴量を、今の設計で作り直す。書き換えた件数を返す。

    特徴量を足したり直したりしたときに使う。勝率の実測には 1 デッキ 7 秒かかるので、
    貯めた勝率はそのまま活かす。デッキ本体を残していない古い行は触れない。
    """
    samples = load_samples(path)
    if not samples:
        return 0
    updated = 0
    rebuilt: list[Sample] = []
    for sample in samples:
        if not sample.decklist:
            rebuilt.append(sample)
            continue
        fresh = deck_features(sample.decklist)
        if fresh == sample.features:
            rebuilt.append(sample)
            continue
        rebuilt.append(replace(sample, features=fresh))
        updated += 1
    if updated:
        path.write_text("".join(sample.to_json() + "\n" for sample in rebuilt), encoding="utf-8")
    return updated


def append_samples(path: Path, samples: list[Sample]) -> int:
    """データを追記する。同じ条件で測り済みのものは書かない。書いた件数を返す。"""
    known = {sample.key() for sample in load_samples(path)}
    fresh: list[Sample] = []
    for sample in samples:
        if sample.key() in known:
            continue
        known.add(sample.key())
        fresh.append(sample)
    if not fresh:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for sample in fresh:
            handle.write(sample.to_json() + "\n")
    return len(fresh)
