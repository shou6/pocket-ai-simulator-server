"""limitless からページを取得するクライアント。

`docs/data-sources.md` 2 節の原則を守る。

- レート制限（既定で 1 リクエスト/秒）
- 同じページを繰り返し取得せず `data/raw/` にキャッシュする
- User-Agent に連絡先を含める
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

import httpx

BASE_URL = "https://play.limitlesstcg.com"
"""取得元のベース URL。"""

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
"""取得元に送る User-Agent。一般的なブラウザと同じものを使う。

**リポジトリの URL や連絡先を入れない。** 非公開リポジトリの名前を外部サイトに
送ることになり、こちらの情報が漏れる（2026-09-10 のユーザー指摘）。
取得元への配慮はアクセス間隔（`DEFAULT_MIN_INTERVAL`）で行う。
"""

DEFAULT_MIN_INTERVAL = 1.0
"""連続取得の最小間隔（秒）。"""

DEFAULT_TIMEOUT = 30.0
"""1 リクエストのタイムアウト（秒）。"""


class LimitlessClient:
    """limitless のページを取得する。取得結果はディスクにキャッシュする。"""

    def __init__(
        self,
        cache_dir: Path,
        *,
        base_url: str = BASE_URL,
        user_agent: str = USER_AGENT,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cache_dir = cache_dir
        self.base_url = base_url
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.timeout = timeout
        self._sleep = sleep
        self._now = now
        self._last_request_at: float | None = None

    def get(self, path: str, *, use_cache: bool = True) -> str:
        """ページを取得して本文を返す。

        Args:
            path: ベース URL からの相対パス。絶対 URL も渡せる。
            use_cache: キャッシュがあればそれを使うか。

        Returns:
            ページの HTML。
        """
        url = urljoin(self.base_url, path)
        cache_path = self._cache_path(url)

        if use_cache and cache_path.exists():
            return cache_path.read_text(encoding="utf-8")

        self._wait_for_rate_limit()
        body = self._fetch(url)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(body, encoding="utf-8")
        return body

    def _cache_path(self, url: str) -> Path:
        """URL に対応するキャッシュファイルの場所。"""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.html"

    def _wait_for_rate_limit(self) -> None:
        """前回の取得から最小間隔が空くまで待つ。"""
        if self._last_request_at is not None:
            elapsed = self._now() - self._last_request_at
            remaining = self.min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._now()

    def _fetch(self, url: str) -> str:
        """実際に HTTP で取得する。テストではここを差し替える。"""
        response = httpx.get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text
