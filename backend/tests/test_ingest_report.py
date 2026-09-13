"""メタの更新の点検（`ingest.report`）のテスト。"""

import copy
import json
from pathlib import Path

from pocket_api.ingest.report import deck_problems, render

SNAPSHOT = json.loads(
    Path("/workspace/data/meta/2026-09-07_B4a-standard.json").read_text(encoding="utf-8")
)


def test_every_decklist_of_the_snapshot_can_be_evaluated() -> None:
    assert deck_problems(SNAPSHOT) == []


def test_reports_unimplemented_cards_and_broken_lists() -> None:
    broken = copy.deepcopy(SNAPSHOT)
    broken["archetypes"][0]["decklists"][0] += "\n1 Z9 999"
    broken["archetypes"][1]["decklists"] = []
    reasons = [p.reason for p in deck_problems(broken)]
    assert any("未実装" in r for r in reasons)
    assert any("1 件も無い" in r for r in reasons)


def test_compares_ranks_with_the_previous_snapshot() -> None:
    newer = copy.deepcopy(SNAPSHOT)
    newer["fetched_at"] = "2026-09-13T00:00:00+00:00"
    newer["archetypes"] = newer["archetypes"][:9]
    newer["archetypes"][0]["slug"] = "brand-new-deck"
    text = render(SNAPSHOT, newer)
    assert "**新しく入った**" in text
    assert "圏外に出た" in text
    assert "なし（すべてのデッキリストをエンジンで評価できる）" in text
