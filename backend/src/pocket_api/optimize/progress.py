"""長い処理の進み具合（仕事の量と残り時間）。

画面には割合（%）ではなく残り時間を出す（`docs/ui-design.md` 3 節）。
探索は途中で止まることがあるので、`total` は上限の見込み。止まった時点で `reach` で先へ進める。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkStatus:
    done: int
    total: int
    remaining_seconds: int | None
    """仕事を始めてからの経過時間と済んだ割合から出した残り時間。

    まだ何も済んでいなければ `None`。
    """


@dataclass
class WorkMeter:
    """済んだ仕事の量。デッキの評価（探索と同じ試合数で環境と戦わせる）1 回を 1 とする。

    残り時間は `begin` からの経過で測る。学習データの読み込みなど、仕事の前の準備の時間を混ぜると、
    最初の数件で残り時間が数倍に出てしまう（2026-09-14 の実測で 4 倍）。
    """

    total: int = 0
    done: int = 0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _started: float | None = field(default=None, repr=False)

    def begin(self, total: int) -> None:
        self.total = total
        self._started = self.clock()

    def advance(self, amount: int = 1) -> None:
        self.done += amount
        if self.total:
            self.done = min(self.done, self.total)

    def reach(self, amount: int) -> None:
        """少なくともここまで済んだことにする（探索が早く止まったとき）。"""
        self.done = max(self.done, min(amount, self.total) if self.total else amount)

    def status(self) -> WorkStatus | None:
        if not self.total:
            return None
        remaining = None
        if self.done and self._started is not None:
            elapsed = self.clock() - self._started
            remaining = round(elapsed * (self.total - self.done) / self.done)
        return WorkStatus(done=self.done, total=self.total, remaining_seconds=remaining)
