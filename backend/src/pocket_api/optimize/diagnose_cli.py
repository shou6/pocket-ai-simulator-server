"""デッキ診断の CLI。

自分のデッキがメタ環境のどこに強く、どこに弱いかを表にする（要件 F-07）。

    uv run python -m pocket_api.optimize.diagnose_cli --deck data/decks/mydeck_sample002.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pocket_api.cards.japanese import Translator
from pocket_api.ingest.snapshot import latest_snapshot_path
from pocket_api.optimize.diagnose import Diagnosis, diagnose, unimplemented_cards

COIN_FLIP_GAP = 0.10
"""先攻後攻の総合勝率がこれ以上ひらいたら、読み方で注意を促す。"""

REPO_ROOT = Path(__file__).resolve().parents[4]
# 既定は data/meta のいちばん新しいスナップショット（メタを更新したら自動で追従する）
DEFAULT_SNAPSHOT = latest_snapshot_path(REPO_ROOT / "data" / "meta")


def render(diagnosis: Diagnosis, title: str, translator: Translator | None = None) -> str:
    """診断の結果を、そのまま読める表にする。"""
    name_of = (lambda text: translator.text(text)) if translator else (lambda text: text)
    lines = [
        f"# {title} の診断",
        "",
        f"メタ環境への期待勝率 **{diagnosis.overall:.1%}**"
        f"（方策 `{diagnosis.strategy}` / 1 組あたり {diagnosis.matchups[0].games} 試合）",
        "",
        f"先攻 **{diagnosis.overall_going_first:.1%}** / "
        f"後攻 **{diagnosis.overall_going_second:.1%}**"
        f"（差 {diagnosis.overall_going_first - diagnosis.overall_going_second:+.1%}）",
        "",
        "いずれも使用率で重み付けした値。相手ごとの勝率は下の表を見る。",
        "",
        "## 相手ごとの相性",
        "",
        "| 相手 | 使用率 | 勝率 | 95% 区間 | 先攻 | 後攻 |",
        "| --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for matchup in diagnosis.matchups:
        low, high = matchup.interval
        lines.append(
            f"| {name_of(matchup.name)} | {matchup.share:.1%} | {matchup.win_rate:.1%} "
            f"| {low:.1%}〜{high:.1%} | {matchup.going_first:.1%} | {matchup.going_second:.1%} |"
        )

    strong = [m for m in diagnosis.matchups if m.win_rate >= 0.55]
    weak = [m for m in diagnosis.matchups if m.win_rate <= 0.45]
    lines += ["", "## 読み方", ""]
    if strong:
        names = "、".join(name_of(m.name) for m in strong)
        lines.append(f"- **有利**（55% 以上）: {names}")
    if weak and len(weak) < len(diagnosis.matchups):
        names = "、".join(name_of(m.name) for m in weak)
        lines.append(f"- **不利**（45% 以下）: {names}")
    if not strong and not weak:
        lines.append("- どの相手ともおおむね互角（45〜55%）")

    # 全部が不利（または全部が有利）だと、そう並べても読み取れない。
    # そういうデッキでは相対的な強弱のほうが役に立つ
    if len(weak) == len(diagnosis.matchups) or len(strong) == len(diagnosis.matchups):
        best = diagnosis.matchups[0]
        worst = diagnosis.matchups[-1]
        side = "勝ち越せていない" if not strong else "負け越していない"
        lines.append(
            f"- どの相手にも{side}。**相対的にましなのは"
            f"「{name_of(best.name)}」（{best.win_rate:.1%}）**で、"
            f"最も苦手なのは「{name_of(worst.name)}」（{worst.win_rate:.1%}）"
        )

    # 使用率が高い相手に弱いと、実際に当たったときの損が大きい
    heavy = max(diagnosis.matchups, key=lambda m: m.share)
    lines.append(
        f"- 最もよく当たるのは「{name_of(heavy.name)}」（使用率 {heavy.share:.1%}）で、"
        f"そこへの勝率は {heavy.win_rate:.1%}"
    )

    # 先攻後攻で総合が大きくひらくデッキは、実力より先攻の取り合いで決まってしまう
    gap = diagnosis.overall_going_first - diagnosis.overall_going_second
    if abs(gap) >= COIN_FLIP_GAP:
        side = "先攻" if gap > 0 else "後攻"
        low = min(diagnosis.overall_going_first, diagnosis.overall_going_second)
        high = max(diagnosis.overall_going_first, diagnosis.overall_going_second)
        lines.append(
            f"- **{side}のときが {abs(gap):.1%} 高い。** この差はコインの表裏で決まるので、"
            f"実際の勝率は {low:.1%}〜{high:.1%} に散らばる"
        )

    swung = [m for m in diagnosis.matchups if abs(m.going_first - m.going_second) >= 0.20]
    if swung:
        names = "、".join(
            f"{name_of(m.name)}（先攻 {m.going_first:.0%} / 後攻 {m.going_second:.0%}）"
            for m in swung
        )
        lines.append(f"- 先攻と後攻で 20 ポイント以上ひらく相手: {names}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, required=True, help="診断するデッキファイル")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--strategy", default="l", help="使う方策（既定は校正済みの l）")
    parser.add_argument("--games", type=int, default=200, help="1 組あたりの試合数")
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

    result = diagnose(
        decklist,
        args.snapshot,
        strategy=args.strategy,
        games=args.games,
        seed=args.seed,
    )
    try:
        translator: Translator | None = Translator.from_file()
    except (FileNotFoundError, ValueError):
        translator = None
    text = render(result, args.name or args.deck.stem, translator)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"{args.out} に書き出しました")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
