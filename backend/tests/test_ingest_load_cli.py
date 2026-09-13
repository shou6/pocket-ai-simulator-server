"""スナップショットを DB に取り込む CLI のテスト。

取得（`pocket_api.ingest`）はファイルに保存するところまで。
DB への取り込みは別のコマンドに分ける（ファイルが正本で、DB は作り直せる派生データ）。
"""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pocket_api.db.models import MetaSnapshot
from pocket_api.ingest.load_cli import load_snapshots

META_DIR = Path("/workspace/data/meta")


def test_loads_every_snapshot_in_a_directory(db_session: Session) -> None:
    loaded = load_snapshots(db_session, META_DIR)
    db_session.flush()
    files = sorted(META_DIR.glob("*.json"))
    assert loaded == len(files), "ディレクトリ内のスナップショットを全部取り込む"
    assert db_session.scalar(select(func.count()).select_from(MetaSnapshot)) == len(files)


def test_loading_twice_does_not_duplicate(db_session: Session) -> None:
    load_snapshots(db_session, META_DIR)
    db_session.flush()
    load_snapshots(db_session, META_DIR)
    db_session.flush()
    files = sorted(META_DIR.glob("*.json"))
    assert db_session.scalar(select(func.count()).select_from(MetaSnapshot)) == len(files)


def test_accepts_a_single_file(db_session: Session) -> None:
    target = sorted(META_DIR.glob("*.json"))[0]
    assert load_snapshots(db_session, target) == 1
