"""スナップショットを DB に取り込むテスト（F-01 の土台）。

取得は JSON ファイルに残す方針（`docs/data-sources.md` 4 節）なので、
DB への取り込みはファイルを読んで入れ直せる形にする。
"""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pocket_api.db.models import MetaDeck, MetaMatchup, MetaSnapshot
from pocket_api.ingest.store import import_snapshot

SNAPSHOT = Path("/workspace/data/meta/2026-09-07_B4a-standard.json")


def test_import_creates_the_snapshot_with_its_decks(db_session: Session) -> None:
    snapshot = import_snapshot(db_session, SNAPSHOT)
    db_session.flush()

    assert snapshot.format == "standard"
    assert snapshot.set_code == "B4a"
    assert len(snapshot.decks) == 10
    top = snapshot.decks[0]
    assert top.rank == 1
    assert top.name == "Mega Lucario ex Lucario"
    assert top.share > 0
    assert top.decklists, "代表デッキリストを持つ"


def test_import_keeps_the_real_matchups(db_session: Session) -> None:
    """実戦の相性は校正の正解データなので落とさない。"""
    import_snapshot(db_session, SNAPSHOT)
    db_session.flush()

    raw = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    expected = sum(len(a["matchups"]) for a in raw["archetypes"])
    stored = db_session.scalar(select(func.count()).select_from(MetaMatchup))
    assert stored == expected


def test_import_is_idempotent(db_session: Session) -> None:
    """同じファイルを二度取り込んでも重複しない。取得は追記でも DB は 1 行。"""
    first = import_snapshot(db_session, SNAPSHOT)
    db_session.flush()
    second = import_snapshot(db_session, SNAPSHOT)
    db_session.flush()

    assert first.id == second.id
    assert db_session.scalar(select(func.count()).select_from(MetaSnapshot)) == 1
    assert db_session.scalar(select(func.count()).select_from(MetaDeck)) == 10
