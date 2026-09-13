"""カードの和名を Bulbapedia から集めて `data/cards/names_ja.json` に保存する。

    uv run python -m pocket_api.ingest.names_ja

対象は deckgym-core が知っているすべてのカード。取得した API 応答は
`data/raw/bulbapedia/` にキャッシュするので、再実行しても取り直さない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pocket_engine_py as engine

from pocket_api.ingest.bulbapedia import BASE_URL, build_names_file, fetch_card_names
from pocket_api.ingest.client import LimitlessClient

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw" / "bulbapedia"
DEFAULT_OUT = REPO_ROOT / "data" / "cards" / "names_ja.json"
DEFAULT_OVERRIDES = REPO_ROOT / "data" / "cards" / "name_overrides.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="応答のキャッシュ先")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="保存先")
    parser.add_argument(
        "--overrides", type=Path, default=DEFAULT_OVERRIDES, help="手動登録の和名（任意）"
    )
    args = parser.parse_args()

    cards = [(status.id, status.name) for status in engine.card_statuses_all()]
    client = LimitlessClient(cache_dir=args.raw_dir, base_url=BASE_URL)
    found = fetch_card_names(client, cards)
    overrides = (
        json.loads(args.overrides.read_text(encoding="utf-8")) if args.overrides.exists() else None
    )
    data = build_names_file(cards, found, overrides)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(data['cards'])} / {len(cards)} 枚の和名を {args.out} に保存")
    if data["missing"]:
        print(f"和名が見つからなかったカード: {len(data['missing'])} 枚")
        for card_id in data["missing"][:30]:
            print(f"  {card_id} {dict(cards)[card_id]}")


if __name__ == "__main__":
    main()
