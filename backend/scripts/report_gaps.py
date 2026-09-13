"""実戦とシミュレーションのずれを一覧にまとめる。

どの組で、どちら向きに、どれだけ外しているのかを見るための表を作る。
個別の対戦ログは `report_matchup.py` で出す。

    uv run python scripts/report_gaps.py --games 250
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from compare_strategies import (
    MIN_MATCHES,
    _is_usable,
    collect_pairs,
    pearson,
    simulate,
    spearman,
)
from report_matchup import _tidy

from pocket_api.cards.japanese import Translator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "meta" / "2026-09-07_B4a-standard.json"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "analysis"

# ずれが大きいと見なす境目
LARGE_GAP = 0.20


def load(snapshot: Path) -> list[dict[str, Any]]:
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    rows = []
    for row in data["archetypes"]:
        row["decklists"] = [d for d in row["decklists"] if _is_usable(d)]
        if row["decklists"]:
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--strategy", default="l", help="使う方策コード")
    parser.add_argument("--games", type=int, default=250, help="1 組あたりの試合数")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    rows = load(args.snapshot)
    tr = Translator.from_file()
    names = [tr.text(r["name"]) for r in rows]
    ranks = [r["rank"] for r in rows]
    decks = [r["decklists"] for r in rows]
    pairs = collect_pairs(rows)
    predicted, elapsed = simulate(decks, pairs, args.strategy, args.games, args.seed)
    actual = [p.actual for p in pairs]

    records = []
    for index, pair in enumerate(pairs):
        gap = predicted[index] - pair.actual
        records.append(
            {
                "rank_a": ranks[pair.a],
                "rank_b": ranks[pair.b],
                "name_a": names[pair.a],
                "name_b": names[pair.b],
                "actual": pair.actual,
                "matches": pair.matches,
                "predicted": predicted[index],
                "gap": gap,
            }
        )
    records.sort(key=lambda r: -abs(r["gap"]))

    lines: list[str] = []
    lines.append(f"# 実戦とシミュレーションのずれ（方策 `{args.strategy}`）")
    lines.append("")
    lines.append(
        f"データ: `{args.snapshot.name}` / 1 組 {args.games} 試合 / シード {args.seed} "
        f"/ 実行 {elapsed:.1f} 秒"
    )
    lines.append("")
    lines.append("## 1. 読み方")
    lines.append("")
    lines.append("各行は「デッキA 対 デッキB」の 1 組。勝率はすべて **デッキA から見た値**。")
    lines.append("")
    lines.append("| 列 | 意味 |")
    lines.append("| --- | --- |")
    lines.append("| 実戦 | limitless のトーナメント結果。この組の実際の勝率 |")
    lines.append(f"| 試合 | 実戦データの試合数。{MIN_MATCHES} 試合未満の組は除外している |")
    lines.append("| シミュ | 同じ 2 デッキをシミュレータで対戦させた勝率 |")
    lines.append("| ずれ | シミュ − 実戦。プラスならシミュレータが A を過大評価している |")
    lines.append("")

    lines.append("## 2. 全体の一致度")
    lines.append("")
    lines.append("| 指標 | 値 |")
    lines.append("| --- | ---: |")
    lines.append(f"| 対象の組 | {len(records)} |")
    lines.append(f"| ピアソン相関 | {pearson(predicted, actual):+.3f} |")
    lines.append(f"| スピアマン相関 | {spearman(predicted, actual):+.3f} |")
    mae = sum(abs(r["gap"]) for r in records) / len(records)
    lines.append(f"| 平均絶対誤差 | {mae:.1%} |")
    large = [r for r in records if abs(r["gap"]) >= LARGE_GAP]
    lines.append(f"| ずれが {LARGE_GAP:.0%} 以上の組 | {len(large)} / {len(records)} |")
    lines.append("")

    lines.append("## 3. ずれの大きい順")
    lines.append("")
    lines.append("| 順 | デッキA | デッキB | 実戦 | 試合 | シミュ | ずれ |")
    lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: |")
    for index, r in enumerate(records):
        mark = "**" if abs(r["gap"]) >= LARGE_GAP else ""
        lines.append(
            f"| {index + 1} | {r['name_a']} | {r['name_b']} | {r['actual']:.1%} "
            f"| {r['matches']} | {r['predicted']:.1%} | {mark}{r['gap']:+.1%}{mark} |"
        )
    lines.append("")

    lines.append("## 4. アーキタイプ別の傾向")
    lines.append("")
    lines.append("そのアーキタイプが関わる組で、平均してどちら向きに外しているか。")
    lines.append("")
    lines.append("| アーキタイプ | 関わる組 | 平均のずれ | 平均絶対誤差 |")
    lines.append("| --- | ---: | ---: | ---: |")
    per_deck: dict[str, list[float]] = {}
    for r in records:
        per_deck.setdefault(r["name_a"], []).append(r["gap"])
        # B 側から見ると符号が反転する
        per_deck.setdefault(r["name_b"], []).append(-r["gap"])
    for name, gaps in sorted(per_deck.items(), key=lambda kv: -abs(sum(kv[1]) / len(kv[1]))):
        mean = sum(gaps) / len(gaps)
        abs_mean = sum(abs(g) for g in gaps) / len(gaps)
        lines.append(f"| {name} | {len(gaps)} | {mean:+.1%} | {abs_mean:.1%} |")
    lines.append("")

    lines.append("## 5. 対戦ログを見るべき組")
    lines.append("")
    lines.append("ずれの大きい上位 5 組。次のコマンドで対戦ログを出せる。")
    lines.append("")
    lines.append("```bash")
    for r in records[:5]:
        lines.append(
            f"uv run python scripts/report_matchup.py "
            f"--a {r['rank_a']} --b {r['rank_b']} --strategy {args.strategy}"
        )
    lines.append("```")
    lines.append("")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"gap-{args.snapshot.stem}-{args.strategy.replace(':', '')}.md"
    path.write_text(_tidy(lines), encoding="utf-8")
    print(f"{path} に書き出しました")
    for r in records[:5]:
        print(
            f"  {r['rank_a']:2} vs {r['rank_b']:2}  {r['gap']:+.1%}  {r['name_a']} / {r['name_b']}"
        )


if __name__ == "__main__":
    main()
