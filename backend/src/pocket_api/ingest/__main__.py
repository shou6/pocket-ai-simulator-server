"""メタデッキのスナップショットを取得して `data/meta/` に保存する。

    uv run python -m pocket_api.ingest --top 10 --decklists 5 --no-cache

`--set` を省くと、limitless がいま既定にしている（最新の）セットを調べて使う。

取得元の利用条件は `docs/data-sources.md` を参照。レート制限を守り、
取得したままの HTML は `data/raw/` にキャッシュする。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from pocket_api.ingest.client import LimitlessClient
from pocket_api.ingest.limitless import parse_displayed_set
from pocket_api.ingest.snapshot import build_snapshot, snapshot_filename

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw" / "limitless"
DEFAULT_META_DIR = REPO_ROOT / "data" / "meta"
LATEST = "latest"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", default="standard", help="フォーマット（既定: standard）")
    parser.add_argument(
        "--set",
        dest="card_set",
        default=LATEST,
        help="対象セット（既定: latest＝limitless がいま既定にしているセット）",
    )
    parser.add_argument("--top", type=int, default=10, help="取得する上位アーキタイプ数")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="HTML キャッシュ先")
    parser.add_argument("--meta-dir", type=Path, default=DEFAULT_META_DIR, help="保存先")
    parser.add_argument("--no-cache", action="store_true", help="キャッシュを使わず取り直す")
    parser.add_argument(
        "--combine",
        action="store_true",
        help="派生デッキを 1 アーキタイプに統合する（試合数が増え誤差が縮む）",
    )
    parser.add_argument(
        "--decklists", type=int, default=1, help="1 アーキタイプあたりのデッキリスト数"
    )
    parser.add_argument("--suffix", default="", help="ファイル名に付ける接尾辞")
    args = parser.parse_args()

    client = LimitlessClient(cache_dir=args.raw_dir)
    if args.no_cache:
        original = client.get

        def get(path: str, *, use_cache: bool = True) -> str:
            return original(path, use_cache=False)

        client.get = get  # type: ignore[method-assign]

    if args.card_set == LATEST:
        # セットを付けずに一覧を開くと、limitless が既定にしているセットが出る。毎回取り直す
        page = client.get(f"/decks?game=POCKET&format={args.format}", use_cache=False)
        args.card_set = parse_displayed_set(page)
        print(f"limitless の既定のセット: {args.card_set}")

    snapshot = build_snapshot(
        client,
        fmt=args.format,
        card_set=args.card_set,
        top=args.top,
        combine=args.combine,
        decklists_per_archetype=args.decklists,
    )

    args.meta_dir.mkdir(parents=True, exist_ok=True)
    # ファイル名の日付はローカル時刻（devcontainer は Asia/Tokyo）。
    # 取得時刻そのものはスナップショット内の fetched_at に UTC で入る
    date = datetime.now().astimezone().strftime("%Y-%m-%d")
    name = snapshot_filename(date, args.format, args.card_set)
    if args.suffix:
        name = name.replace(".json", f"-{args.suffix}.json")
    path = args.meta_dir / name
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lists = sum(len(a["decklists"]) for a in snapshot["archetypes"])
    missing = [a["name"] for a in snapshot["archetypes"] if not a["decklists"]]
    print(f"{len(snapshot['archetypes'])} アーキタイプ / デッキリスト {lists} 件を {path} に保存")
    if missing:
        print(f"デッキリストを取得できなかったアーキタイプ: {', '.join(missing)}")


if __name__ == "__main__":
    main()
