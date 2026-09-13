"""新しいメタのスナップショットを、1 つ前と比べて点検する。

    uv run python -m pocket_api.ingest.report            # data/meta の新しい 2 つを比べる
    uv run python -m pocket_api.ingest.report --new data/meta/2026-09-13_B4a-standard.json

取得したデッキリストがエンジンで評価できるか（未実装カード・20 枚などの規則）を確かめ、
順位・使用率・実戦勝率の変化と、入れ替わったアーキタイプを並べる。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pocket_engine_py as engine

from pocket_api.cards.catalog import translator
from pocket_api.optimize.diagnose import unimplemented_cards
from pocket_api.optimize.recipe import DeckRecipe

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_META_DIR = REPO_ROOT / "data" / "meta"


@dataclass(frozen=True)
class DeckProblem:
    archetype: str
    index: int
    reason: str


def deck_problems(snapshot: dict[str, Any]) -> list[DeckProblem]:
    """評価できないデッキリスト。未実装カードと、エンジンの規則に合わないもの。"""
    found: list[DeckProblem] = []
    for archetype in snapshot["archetypes"]:
        if not archetype["decklists"]:
            found.append(DeckProblem(archetype["name"], 0, "デッキリストが 1 件も無い"))
        for index, text in enumerate(archetype["decklists"], start=1):
            blocked = unimplemented_cards(text)
            if blocked:
                names = "、".join(f"{card_id} {name}" for card_id, name, _ in blocked)
                found.append(DeckProblem(archetype["name"], index, f"未実装のカード: {names}"))
                continue
            try:
                engine.validate_deck(DeckRecipe.from_text(text).to_text())
            except ValueError as error:
                found.append(DeckProblem(archetype["name"], index, str(error)))
    return found


def render(old: dict[str, Any] | None, new: dict[str, Any]) -> str:
    """点検の結果を Markdown にする。"""
    names = translator()
    top = new["archetypes"][0]
    compared = f"{old['fetched_at'][:10]} のスナップショット" if old else "なし（初回）"
    lines = [
        f"## {new['fetched_at'][:10]}：{new['set']}（{new['format']}）",
        "",
        f"- 取得日時：{new['fetched_at']}",
        f"- 集計したデッキ数：約 {round(top['count'] / top['share']):,}",
        f"- 比べた相手：{compared}",
    ]
    if old and old["set"] != new["set"]:
        lines.append(f"- **セットが変わった**：{old['set']} → {new['set']}")
    lines += [
        "",
        "| 順 | アーキタイプ | 使用率 | 実戦勝率 | 前回 |",
        "| ---: | --- | ---: | ---: | --- |",
    ]
    previous = {a["slug"]: a for a in old["archetypes"]} if old else {}
    for archetype in new["archetypes"]:
        before = previous.get(archetype["slug"])
        was = (
            f"{before['rank']} 位 / {before['share']:.1%} / {before['win_rate']:.1%}"
            if before
            else "**新しく入った**"
        )
        lines.append(
            f"| {archetype['rank']} | {names.text(archetype['name'])} "
            f"| {archetype['share']:.1%} | {archetype['win_rate']:.1%} | {was} |"
        )
    if old:
        current = {a["slug"] for a in new["archetypes"]}
        gone = [names.text(a["name"]) for a in old["archetypes"] if a["slug"] not in current]
        if gone:
            lines += ["", f"圏外に出た：{'、'.join(gone)}"]

    problems = deck_problems(new)
    lines += ["", "### 評価できないデッキリスト", ""]
    if problems:
        lines += [f"- {p.archetype} の {p.index} 件目：{p.reason}" for p in problems]
    else:
        lines.append("なし（すべてのデッキリストをエンジンで評価できる）")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new", type=Path, help="点検するスナップショット（既定は最新）")
    parser.add_argument("--old", type=Path, help="比べるスナップショット（既定は 1 つ前）")
    args = parser.parse_args(argv)

    files = sorted(DEFAULT_META_DIR.glob("*.json"))
    new_path = args.new or (files[-1] if files else None)
    if new_path is None:
        print("スナップショットがありません")
        return 1
    earlier = [f for f in files if f.name < new_path.name]
    old_path = args.old or (earlier[-1] if earlier else None)
    new = json.loads(new_path.read_text(encoding="utf-8"))
    old = json.loads(old_path.read_text(encoding="utf-8")) if old_path else None
    print(render(old, new), end="")
    return 1 if deck_problems(new) else 0


if __name__ == "__main__":
    raise SystemExit(main())
