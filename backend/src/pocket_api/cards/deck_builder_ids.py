"""デッキ QR で使うカード番号（deckBuilderNr）の対応表。

    uv run python -m pocket_api.cards.deck_builder_ids

flibustier のカードデータ（MIT）の `image` フィールドがゲームのアセット名を持っており、
`cPK_10_000010_00_FUSHIGIDANE_C.webp` の 6 桁を 10 で割ると deckBuilderNr になる
（`docs/deck-qr.md` 3 節）。`data/raw/` は git 管理外なので、生成した対応表を
`data/cards/deck_builder_ids.json` に置く。新セットが出たら作り直す。
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SOURCE = REPO_ROOT / "data" / "raw" / "flibustier" / "dist" / "cards.json"
DEFAULT_OUT = REPO_ROOT / "data" / "cards" / "deck_builder_ids.json"

SCHEMA_VERSION = 1
SOURCE = "flibustier/pokemon-tcg-pocket-database (MIT) の image フィールド"

_ASSET = re.compile(r"^c(PK|TR)_\d+_(\d{6})_")


def _set_sort_key(card_id: str) -> tuple[str, int]:
    set_code, number = card_id.rsplit(" ", 1)
    return set_code, int(number)


def build_ids_file(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """flibustier の `cards.json` から対応表を作る。

    キーは `種別:番号`（`PK:621`）、値はその番号を持つ印刷のカード ID。
    **プロモのセット名はエンジンに揃える**（`PROMO-A` → `P-A`）。揃えないと
    プロモを含むデッキが `Card ID not found` で落ちる。
    """
    table: dict[str, set[str]] = {}
    for card in cards:
        match = _ASSET.match(card["image"])
        if match is None:
            raise ValueError(f"アセット名の形が想定と違います: {card['image']}")
        digits = int(match.group(2))
        if digits % 10:
            raise ValueError(f"番号が 10 の倍数ではありません: {card['image']}")
        set_code = str(card["set"]).replace("PROMO-", "P-")
        card_id = f"{set_code} {int(card['number']):03d}"
        table.setdefault(f"{match.group(1)}:{digits // 10}", set()).add(card_id)
    ordered = sorted(table.items(), key=lambda item: (item[0][:2], int(item[0][3:])))
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cards": {key: sorted(ids, key=_set_sort_key) for key, ids in ordered},
    }


@dataclass(frozen=True)
class DeckBuilderIds:
    """カード ID と（種別, deckBuilderNr）の相互変換。"""

    prints: dict[tuple[str, int], tuple[str, ...]]
    """（種別, 番号）→ 印刷のカード ID。昇順で、先頭を代表にする。"""

    keys: dict[str, tuple[str, int]]
    """カード ID → （種別, 番号）。"""

    @classmethod
    def from_file(cls, path: Path = DEFAULT_OUT) -> DeckBuilderIds:
        data = json.loads(path.read_text(encoding="utf-8"))
        prints: dict[tuple[str, int], tuple[str, ...]] = {}
        keys: dict[str, tuple[str, int]] = {}
        for raw_key, ids in data["cards"].items():
            kind, number = raw_key.split(":")
            key = (kind, int(number))
            prints[key] = tuple(ids)
            for card_id in ids:
                keys[card_id] = key
        return cls(prints=prints, keys=keys)

    def key_of(self, card_id: str) -> tuple[str, int] | None:
        """そのカードの（種別, 番号）。対応表に無ければ `None`。"""
        return self.keys.get(card_id)

    def representative(self, kind: str, number: int) -> str | None:
        """その番号の代表の印刷。再録のどれを選んでも評価結果は同じ。"""
        found = self.prints.get((kind, number))
        return found[0] if found else None


@cache
def deck_builder_ids() -> DeckBuilderIds:
    """リポジトリに置いた対応表。プロセス内で一度だけ読む。"""
    return DeckBuilderIds.from_file(DEFAULT_OUT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    data = build_ids_file(json.loads(args.source.read_text(encoding="utf-8")))
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    total = sum(len(ids) for ids in data["cards"].values())
    print(f"{len(data['cards'])} 種（{total} 枚）を {args.out} に保存")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
