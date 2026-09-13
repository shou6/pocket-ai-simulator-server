"""スナップショットを DB に取り込む。

取得（`python -m pocket_api.ingest`）はファイルに保存するところまでを担う。
ファイルが正本で、DB はそこから作り直せる派生データなので、取り込みは別のコマンドに分ける。

    uv run python -m pocket_api.ingest.load_cli
    uv run python -m pocket_api.ingest.load_cli --path data/meta/2026-09-07_B4a-standard.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy.orm import Session

from pocket_api.db.session import session_scope
from pocket_api.ingest.store import import_snapshot

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_META_DIR = REPO_ROOT / "data" / "meta"


def load_snapshots(session: Session, path: Path) -> int:
    """スナップショットを取り込み、取り込んだ件数を返す。

    `path` がディレクトリなら、その中の `*.json` をすべて取り込む。
    同じスナップショットを二度取り込んでも重複しない（`import_snapshot`）。
    """
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    for file in files:
        import_snapshot(session, file)
    return len(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_META_DIR,
        help="取り込むスナップショット。ディレクトリなら中の *.json をすべて",
    )
    args = parser.parse_args(argv)
    if not args.path.exists():
        print(f"見つかりません: {args.path}")
        return 1
    with session_scope() as session:
        loaded = load_snapshots(session, args.path)
    print(f"{loaded} 件のスナップショットを取り込みました（{args.path}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
