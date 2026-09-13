"""改善提案の CLI。

自分のデッキに対して「どのカードを入れ替えると何%上がるか」を出す（要件 F-03 / F-07）。

    uv run python -m pocket_api.optimize.improve_cli --deck data/decks/mydeck_sample002.txt
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from pocket_api.cards.japanese import Translator
from pocket_api.ingest.snapshot import latest_snapshot_path
from pocket_api.optimize.cli import load_ownership
from pocket_api.optimize.diagnose import unimplemented_cards
from pocket_api.optimize.improve import ImprovementReport, suggest_improvements

REPO_ROOT = Path(__file__).resolve().parents[4]
# 既定は data/meta のいちばん新しいスナップショット（メタを更新したら自動で追従する）
DEFAULT_SNAPSHOT = latest_snapshot_path(REPO_ROOT / "data" / "meta")
DEFAULT_STORE = REPO_ROOT / "data" / "surrogate" / "samples.jsonl"

DELTA_NOISE_AT_200 = 0.010
"""1 枚違いのデッキどうしの差が、1 組 200 試合でどれだけぶれるかの実測値（標準偏差）。

エルフバズーカとその 1 枚入れ替え案を、シードを 5 通り変えて測った。

- 2026-09-09：1.4%（`docs/status.md` 3.15）
- 2026-09-10：**1.0%**。プレイヤーごとに独立した乱数でシャッフルするようにして
  対比の精度が上がった（3.22）。勝率そのもののぶれも 1.2% → 0.7% に下がっている

メタ加重の期待勝率は 10 組 × 200 試合 = 2,000 試合の平均なので、
**1 組ぶんの ±7% を当てはめるのは誤り**である。
"""


def delta_noise(games: int) -> float:
    """1 組 `games` 試合のときの、差のぶれ（標準偏差）。試合数の平方根に反比例する。"""
    return DELTA_NOISE_AT_200 * math.sqrt(200 / max(games, 1))


def judge_delta(delta: float, games: int) -> str:
    """差がぶれと比べてどれだけ確かか（`docs/ui-design.md` 4 節）。

    - `clear`：ぶれの 2 倍を超えて上がる。「上がると言ってよい」
    - `likely`：ぶれを超えるが 2 倍未満。「上がりそう」
    - `none`：ぶれの内側。「差とは言えない」
    - `worse`：ぶれを超えて下がる
    """
    noise = delta_noise(games)
    if delta > 2 * noise:
        return "clear"
    if delta > noise:
        return "likely"
    if delta < -noise:
        return "worse"
    return "none"


def render(
    report: ImprovementReport,
    title: str,
    *,
    games: int,
    strategy: str,
    translator: Translator | None = None,
) -> str:
    """改善提案を、そのまま読める形にする。"""
    name_of = (lambda text: translator.card_name(text)) if translator else (lambda text: text)
    lines = [
        f"# {title} の改善提案",
        "",
        f"元のデッキのメタ環境への期待勝率 **{report.base_win_rate:.1%}**"
        f"（方策 `{strategy}` / 1 組あたり {games} 試合）",
        "",
        f"1 枚入れ替えの候補 {report.screened} 通りをモデルの予測で絞り、"
        f"上位 {report.evaluated} 通りを実際にシミュレーションした。",
        "",
    ]
    if report.model_note:
        lines += [f"絞り込みに使ったモデル: {report.model_note}", ""]
    if not report.suggestions:
        lines.append("入れ替えの候補が見つからなかった。")
        return "\n".join(lines) + "\n"

    lines += [
        "## 入れ替えの候補",
        "",
        "| 抜く | 入れる | 入れ替え後 | 差 |",
        "| --- | --- | ---: | ---: |",
    ]
    for item in report.suggestions:
        lines.append(
            f"| {name_of(item.out_card)} | {name_of(item.in_card)} "
            f"| {item.win_rate:.1%} | {item.delta:+.1%} |"
        )

    best = report.suggestions[0]
    noise = delta_noise(games)
    threshold = 2 * noise
    lines += ["", "## 読み方", ""]
    if best.delta <= 0:
        lines.append("- **上がる入れ替えは見つからなかった。** 今の構成のほうが良い")
    elif best.delta < threshold:
        lines.append(
            f"- 最良は「{name_of(best.out_card)} → {name_of(best.in_card)}」で"
            f"{best.delta:+.1%}。ただし 1 組 {games} 試合では差のぶれが "
            f"±{noise:.1%}（標準偏差）あり、**{threshold:.1%} を超えないと差があるとは"
            f"言い切れない**。`--games {games * 4}` にすればぶれは半分になる"
        )
    else:
        lines.append(
            f"- **「{name_of(best.out_card)} → {name_of(best.in_card)}」で"
            f"{best.delta:+.1%}**（{report.base_win_rate:.1%} → {best.win_rate:.1%}）"
        )
    lines.append(
        f"- 差が ±{noise:.1%}（1 組 {games} 試合での実測のぶれ）以内に収まる候補どうしは、"
        "並び順に意味がない"
    )
    lines.append(
        "- ここに出るのは 1 枚入れ替えの範囲。構成そのものを変える提案は `optimize.cli` を使う"
    )
    if best.delta > 0:
        lines += ["", "## 最良の候補のデッキリスト", "", "```text", best.decklist.strip(), "```"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, required=True, help="改善するデッキファイル")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--strategy", default="l", help="使う方策（既定は校正済みの l）")
    parser.add_argument("--games", type=int, default=200, help="1 組あたりの試合数")
    parser.add_argument(
        "--candidates",
        type=int,
        default=20,
        help="実評価に回す候補の数。増やすほど当たりに届く（40 で到達、1 件 20 秒ほど）",
    )
    parser.add_argument(
        "--neighbours",
        type=int,
        default=None,
        help="モデルに渡す前に候補を間引く数。既定は間引かない",
    )
    parser.add_argument(
        "--similar-width",
        type=int,
        default=16,
        help="1 枚を入れ替えるとき、役割の近い何枚を候補にするか",
    )
    parser.add_argument(
        "--per-slot",
        type=int,
        default=2,
        help="同じ札を抜く案をいくつまで実評価に回すか。抜く札を散らして選択肢を広げる",
    )
    parser.add_argument("--owned", type=Path, help="所持カードの JSON。持っている範囲に絞る")
    parser.add_argument(
        "--surrogate",
        type=Path,
        default=DEFAULT_STORE,
        help="サロゲートモデルの学習データ。あれば予測で候補を絞る",
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--name", help="表示名（既定はファイル名）")
    parser.add_argument("--out", type=Path, help="書き出し先（既定は標準出力）")
    args = parser.parse_args(argv)

    decklist = args.deck.read_text(encoding="utf-8")
    blocked = unimplemented_cards(decklist)
    if blocked:
        print("このデッキは評価できません。未実装のカードが含まれています。", file=sys.stderr)
        for card_id, name, reason in blocked:
            print(f"  {card_id} {name}: {reason}", file=sys.stderr)
        return 1

    report = suggest_improvements(
        decklist,
        args.snapshot,
        strategy=args.strategy,
        games=args.games,
        candidates=args.candidates,
        neighbours_limit=args.neighbours,
        similar_width=args.similar_width,
        per_slot=args.per_slot,
        ownership=load_ownership(args.owned),
        store=args.surrogate,
        seed=args.seed,
    )
    try:
        translator: Translator | None = Translator.from_file()
    except (FileNotFoundError, ValueError):
        translator = None
    text = render(
        report,
        args.name or args.deck.stem,
        games=args.games,
        strategy=args.strategy,
        translator=translator,
    )
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"{args.out} に書き出しました")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
