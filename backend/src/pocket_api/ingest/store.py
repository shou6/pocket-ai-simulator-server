"""スナップショットの JSON を DB に取り込む。

取得したものはファイルとして残す方針（`docs/data-sources.md` 4 節）なので、
DB はそこから作り直せる派生データとして扱う。同じファイルを何度取り込んでも
同じ状態になるようにする。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pocket_api.db.models import MetaDeck, MetaMatchup, MetaSnapshot


def import_snapshot(session: Session, path: Path) -> MetaSnapshot:
    """スナップショットを取り込み、`MetaSnapshot` を返す。

    同じ（取得日時・フォーマット・セット）の行があれば、その中身を入れ替える。
    取得のたびにファイルは増えるが、DB の 1 行に対応するのは 1 ファイル。
    """
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    fetched_at = datetime.fromisoformat(raw["fetched_at"])
    format_ = str(raw["format"])
    set_code = str(raw["set"])

    snapshot = session.scalar(
        select(MetaSnapshot).where(
            MetaSnapshot.fetched_at == fetched_at,
            MetaSnapshot.format == format_,
            MetaSnapshot.set_code == set_code,
        )
    )
    if snapshot is None:
        snapshot = MetaSnapshot(
            source_url=str(raw["source_url"]),
            fetched_at=fetched_at,
            format=format_,
            set_code=set_code,
            schema_version=int(raw["schema_version"]),
        )
        session.add(snapshot)
    else:
        snapshot.source_url = str(raw["source_url"])
        snapshot.schema_version = int(raw["schema_version"])
        # 中身は作り直す。順位や使用率が変わっていても辻褄が合うようにする
        snapshot.decks.clear()
        session.flush()

    for row in raw.get("archetypes", []):
        deck = MetaDeck(
            rank=int(row["rank"]),
            name=str(row["name"]),
            slug=str(row["slug"]),
            count=int(row["count"]),
            share=float(row["share"]),
            wins=int(row["wins"]),
            losses=int(row["losses"]),
            ties=int(row["ties"]),
            win_rate=float(row["win_rate"]),
            decklists=list(row.get("decklists", [])),
        )
        for matchup in row.get("matchups", []):
            deck.matchups.append(
                MetaMatchup(
                    opponent_slug=str(matchup["opponent_slug"]),
                    matches=int(matchup["matches"]),
                    wins=int(matchup["wins"]),
                    losses=int(matchup["losses"]),
                    ties=int(matchup["ties"]),
                    win_rate=float(matchup["win_rate"]),
                )
            )
        snapshot.decks.append(deck)
    return snapshot
