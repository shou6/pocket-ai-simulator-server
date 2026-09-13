"""序盤の構成が整ったのに負けた試合を探し、全行動ログを書き出す。

進化先が引けずに負けた試合は検証のしようがない。主役が早く立ったのに負けた
試合だけを拾い、方策の問題を探すためのもの。

    uv run python scripts/find_upset_games.py --a 8 --b 6 --main "Mega Blaziken ex" \\
        --by-turn 6 --seeds 200 --logs 3

`--a` が先攻で、その側の `--main` が `--by-turn` までに場に出て（エネルギー付き）負けた
シードを一覧にし、先頭 `--logs` 件の全行動ログを
`docs/analysis/{デッキA}-vs-{デッキB}/upsets/seedN.md` に書き出す。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pocket_engine_py as engine

sys.path.insert(0, str(Path(__file__).parent))

from ai_report import render_ai_report
from board_view import render_replay_html
from report_matchup import (
    DEFAULT_SNAPSHOT,
    REPO_ROOT,
    load,
    matchup_dirname,
    render_decklist,
    render_replay,
)

from pocket_api.cards.japanese import Translator

DEFAULT_ANALYSIS_DIR = REPO_ROOT / "docs" / "analysis"


def main_ready_turn(replay: Any, player: int, main: str) -> int | None:
    """主役がエネルギー付きで場に出た最初のターン。出なければ None。"""
    for step in replay.steps:
        board = list(step.bench[player])
        if step.active[player] is not None:
            board.append(step.active[player])
        if any(p.name == main and p.energy for p in board):
            return int(step.turn)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--a", type=int, required=True, help="先攻側の順位（1 始まり）")
    parser.add_argument("--b", type=int, required=True, help="後攻側の順位（1 始まり）")
    parser.add_argument("--main", required=True, help="先攻側の主役カード名（英名）")
    parser.add_argument("--by-turn", type=int, default=6, help="このターンまでに主役が立った試合")
    parser.add_argument("--strategy", default="l")
    parser.add_argument("--seeds", type=int, default=200, help="調べるシード数（1 から）")
    parser.add_argument("--logs", type=int, default=3, help="全行動ログを書き出す件数")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="既定は docs/analysis/{デッキA}-vs-{デッキB}/upsets/",
    )
    args = parser.parse_args()

    rows = load(args.snapshot)
    tr = Translator.from_file()
    for row in rows:
        row["name"] = tr.text(row["name"])
    a = next(r for r in rows if r["rank"] == args.a)
    b = next(r for r in rows if r["rank"] == args.b)
    deck_a, deck_b = a["decklists"][0], b["decklists"][0]
    out_dir = (
        args.out_dir or DEFAULT_ANALYSIS_DIR / matchup_dirname(a["slug"], b["slug"]) / "upsets"
    )

    ready = 0
    upsets: list[tuple[int, int, Any]] = []
    for seed in range(1, args.seeds + 1):
        replay = engine.replay_game(deck_a, deck_b, args.strategy, args.strategy, seed)
        turn = main_ready_turn(replay, 0, args.main)
        if turn is None or turn > args.by_turn:
            continue
        ready += 1
        if replay.result == "PlayerB":
            upsets.append((seed, turn, replay))

    main_ja = tr.card_name(args.main)
    print(
        f"{args.seeds} 試合中、{main_ja} がターン {args.by_turn} までに立った試合 {ready} 件、"
        f"うち負け {len(upsets)} 件"
    )
    print("| シード | 主役が立ったターン | ターン数 | 最終ポイント |")
    print("| ---: | ---: | ---: | --- |")
    for seed, turn, replay in upsets:
        print(f"| {seed} | {turn} | {replay.turns} | {replay.points[0]} - {replay.points[1]} |")

    out_dir.mkdir(parents=True, exist_ok=True)
    for seed, turn, replay in upsets[: args.logs]:
        lines = [
            f"# {a['name']} 対 {b['name']}（シード {seed}）",
            "",
            f"方策 `{args.strategy}`。{main_ja} がターン {turn} に立ったのに負けた試合。",
            f"結果: {replay.turns} ターン、{replay.points[0]} - {replay.points[1]}。",
            "",
            "## デッキリスト",
            "",
            f"### 先攻: {a['name']}",
            "",
            *render_decklist(deck_a, out_dir, tr),
            "",
            f"### 後攻: {b['name']}",
            "",
            *render_decklist(deck_b, out_dir, tr),
            "",
            "## 全行動",
            "",
            "`先` は先攻、`後` は後攻。「効果:」は deckgym が内部で処理した行動。"
            "ダメージやポイントが動いた行動の後に盤面を、引いた直後と番の終わりに手札を示す。",
            # 直前が `## 全行動` なので、ターンの見出しは h3 にする
            *render_replay(replay, a["name"], b["name"], tr, turn_heading="###"),
        ]
        path = out_dir / f"seed{seed}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{path} に書き出しました")

        board_path = path.with_suffix(".html")
        board_path.write_text(
            render_replay_html(
                replay,
                (a["name"], b["name"]),
                out_dir,
                tr,
                f"{a['name']} 対 {b['name']}（シード {seed}）",
                f"{main_ja} がターン {turn} に立ったのに負けた試合。"
                f"{replay.turns} ターン、{replay.points[0]} - {replay.points[1]}。",
            ),
            encoding="utf-8",
        )
        print(f"{board_path} に盤面つきの HTML を書き出しました")

        ai_path = out_dir / f"ai-seed{seed}.md"
        ai_path.write_text(
            render_ai_report(
                replay,
                (a["name"], b["name"]),
                (deck_a, deck_b),
                tr,
                strategy=args.strategy,
                seed=seed,
                engine_revision=engine.deckgym_revision()[:7],
                focus=(
                    f"先攻の主役 {main_ja} がターン {turn} に場に出てエネルギーも付いたのに、"
                    "先攻が負けた試合。序盤の展開は成功しているので、"
                    "中盤以降の打ち回しに誤りがあるかを見てほしい。"
                ),
            ),
            encoding="utf-8",
        )
        print(f"{ai_path} に AI 分析用のレポートを書き出しました")


if __name__ == "__main__":
    main()
