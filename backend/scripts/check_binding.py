"""バインディングが Rust のソースより新しいか確かめる。

`deckgym_revision()` は手元のフォークの HEAD を返すのでフォークの変更は拾えるが、
**ラッパー（`engine/crates/`）だけを直した場合は値が変わらない**。
`engine_version()` も Cargo のバージョン（`0.1.0`）を返すだけ。
古いバインディングのまま測ると、直したはずの変更が効いていない数字が出る。

同じ判定を `.claude/scripts/guard-stale-binding.js` が PreToolUse フックで行い、
計測コマンドを自動で止める。こちらは手で確かめたいときに使う。

    uv run python scripts/check_binding.py
"""

from __future__ import annotations

from pathlib import Path

import pocket_engine_py as engine

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_SRC = REPO_ROOT / "engine" / "crates"
VENDOR_SRC = REPO_ROOT / "vendor" / "deckgym-core" / "src"


def newest_source() -> tuple[Path, float]:
    """Rust ソースのうち、最後に変更されたものと、その時刻。"""
    newest: tuple[Path, float] | None = None
    for root in (ENGINE_SRC, VENDOR_SRC):
        if not root.exists():
            continue
        for path in root.rglob("*.rs"):
            stamp = path.stat().st_mtime
            if newest is None or stamp > newest[1]:
                newest = (path, stamp)
    if newest is None:
        raise SystemExit("Rust のソースが見つかりません")
    return newest


def main() -> int:
    binding = Path(engine.__file__)
    built = binding.stat().st_mtime
    path, changed = newest_source()

    print(f"バインディング: {binding}")
    print(f"deckgym の版: {engine.deckgym_revision()[:8]}")
    if changed <= built:
        print("最新です（ソースより新しい）")
        return 0

    print(f"**古いです。** {path.relative_to(REPO_ROOT)} のほうが新しい")
    print(f"  ソース更新 {changed - built:.0f} 秒後ろ")
    print("  リポジトリ直下で `make engine-py` を実行してください")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
