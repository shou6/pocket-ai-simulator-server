"""特定の 1 組について、実戦とシミュレーションの食い違いを Markdown にまとめる。

勝敗の数字だけでは、どこで食い違っているのか判断できない。
対戦がどう進んだのかを行動単位で書き出す。

    uv run python scripts/report_matchup.py --a 2 --b 6

`--a` / `--b` はスナップショット内の順位（1 始まり）。
出力先は既定で `docs/analysis/{デッキA}-vs-{デッキB}/report.md`
（`--seed` が既定の 1 以外なら `report-sN.md`、`--strategy` が既定の `l` 以外ならさらに付く）。
デッキの呼び名は `SHORT_NAMES` を見る。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import pocket_engine_py as engine
from ai_report import render_ai_report
from board_view import render_replay_html

from pocket_api.cards.japanese import Translator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "meta" / "2026-09-07_B4a-standard.json"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "analysis"
IMAGE_DIR = REPO_ROOT / "data" / "raw" / "flibustier" / "dist" / "images" / "cards-by-set"
"""flibustier のリリースから展開したカード画像（`docs/data-sources.md` 1 節）。"""

# limitless の slug（`butterfree-b3b-mega-sceptile-ex-b3` 等）は長く、ファイル名にすると
# どれがどれか読み取りにくい。ログのファイル名にだけ使う短い呼び名（B4a 上位 10 固定）。
SHORT_NAMES: dict[str, str] = {
    "mega-lucario-ex-b3-lucario-a2": "lucario",
    "vespiquen-ex-b4-shuckle-ex-a4": "vespiquen",
    "suicune-ex-a4a-baxcalibur-b2a": "suicune",
    "team-rockets-weezing-ex-b4a-hoopa-ex-b4": "weezing",
    "mega-altaria-ex-b1-espeon-b3a": "altaria",
    "butterfree-b3b-mega-sceptile-ex-b3": "butterfree",
    "hydreigon-mega-absol-ex-b1": "hydreigon",
    "mega-blaziken-ex-b1": "blaziken",
    "mega-sceptile-ex-b3-greninja-a1": "greninja",
    "team-rockets-raticate-ex-b4a-alolan-ninetales-ex-b2": "raticate",
}


def short_slug(slug: str) -> str:
    """ログのファイル名に使う短い呼び名。表にないアーキタイプは slug の先頭語だけ使う。"""
    if slug in SHORT_NAMES:
        return SHORT_NAMES[slug]
    return slug.split("-")[0]


def matchup_dirname(slug_a: str, slug_b: str) -> str:
    """この組のログをまとめるフォルダ名: `{デッキA}-vs-{デッキB}`。"""
    return f"{short_slug(slug_a)}-vs-{short_slug(slug_b)}"


def matchup_report_filename(strategy: str, seed: int) -> str:
    """組のフォルダ内でのレポートのファイル名。既定（方策 l・シード 1）なら `report.md`。"""
    name = "report"
    if seed != 1:
        name += f"-s{seed}"
    if strategy != "l":
        name += f"-{strategy.replace(':', '')}"
    return f"{name}.md"


# デッキリストの行: 「2 Caterpie B3b 1」
_DECK_LINE_RE = re.compile(r"^(\d+)\s+(.+?)\s+(P-[AB]|[AB]\d[a-z]?)\s+(\d+)\s*$")

# 記録する試合数。ログを全部載せるのは 1 試合だけにする
SAMPLE_GAMES = 4


def load(snapshot: Path) -> list[dict[str, Any]]:
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    rows = []
    for row in data["archetypes"]:
        usable = []
        for decklist in row["decklists"]:
            try:
                engine.validate_deck(decklist)
                usable.append(decklist)
            except ValueError:
                continue
        row["decklists"] = usable
        rows.append(row)
    return rows


def actual_win_rate(a: dict[str, Any], b: dict[str, Any]) -> tuple[float | None, int]:
    """a から見た b への実戦勝率と試合数。"""
    for matchup in a["matchups"]:
        if matchup["opponent_slug"] == b["slug"]:
            return matchup["win_rate"], matchup["matches"]
    for matchup in b["matchups"]:
        if matchup["opponent_slug"] == a["slug"]:
            return 1.0 - matchup["win_rate"], matchup["matches"]
    return None, 0


def image_path(set_code: str, number: str) -> Path:
    """カード画像の場所。flibustier はプロモを `PROMO-A` と表記する。"""
    folder = set_code.replace("P-", "PROMO-")
    return IMAGE_DIR / folder / f"{number}.webp"


def render_hand(cards: list[Any], tr: Translator) -> str:
    """手札の内容を 1 行で書く。`cards` は `CardRef` の並び。"""
    return "、".join(tr.card_name(card.name) for card in cards) if cards else "なし"


def render_decklist(decklist: str, out_dir: Path, tr: Translator) -> list[str]:
    """デッキリストを和名つきで書き、画像があれば並べる。

    画像は `out_dir` からの相対パスで参照する（サブフォルダに出しても表示できるように）。
    """
    lines: list[str] = ["```text"]
    images: list[str] = []
    for raw in decklist.rstrip().splitlines():
        match = _DECK_LINE_RE.match(raw.strip())
        if match is None:
            lines.append(raw)
            continue
        count, name, set_code, number = match.groups()
        lines.append(f"{count} {tr.card_name(name)}（{name} {set_code} {number}）")
        path = image_path(set_code, number)
        if path.exists():
            rel = Path(os.path.relpath(path, out_dir))
            images.append(f'<img src="{rel.as_posix()}" alt="{name}" width="110">')
    lines.append("```")
    if images:
        lines.append("")
        lines.append("<p>" + "\n".join(images) + "</p>")
    return lines


def render_board(step: Any, player: int, tr: Translator) -> str:
    """片方のプレイヤーの盤面を 1 行で書く。"""
    active = step.active[player]
    if active is None:
        head = "（バトル場が空）"
    else:
        energy = "".join(active.energy) or "-"
        status = f" [{'/'.join(active.status)}]" if active.status else ""
        name = tr.card_name(active.name)
        head = f"**{name}** {active.remaining_hp}/{active.max_hp} E:{energy}{status}"

    bench = step.bench[player]
    if bench:
        parts = [
            f"{tr.card_name(p.name)} {p.remaining_hp}/{p.max_hp} E:{''.join(p.energy) or '-'}"
            for p in bench
        ]
        tail = " ／ ベンチ: " + "、".join(parts)
    else:
        tail = " ／ ベンチ: なし"
    return head + tail


def render_replay(
    replay: Any,
    name_a: str,
    name_b: str,
    tr: Translator,
    turn_heading: str = "####",
) -> list[str]:
    """1 試合の推移を Markdown の行として返す。

    `turn_heading` は呼び出し元の見出し階層に合わせる（見出しは 1 段ずつ下げる）。
    """
    lines: list[str] = []
    names = [name_a, name_b]
    lines.append("")
    lines.append(f"- 先攻の初手: {render_hand(list(replay.opening_hands[0]), tr)}")
    lines.append(f"- 後攻の初手: {render_hand(list(replay.opening_hands[1]), tr)}")
    turn = -1
    for index, step in enumerate(replay.steps):
        if step.turn != turn:
            turn = step.turn
            lines.append("")
            lines.append(f"{turn_heading} ターン {turn}")
            lines.append("")
        marker = "先" if step.actor == 0 else "後"
        # 番号付きリスト（`12.`）にすると、エディタの保存時整形が節ごとに 1 から振り直す。
        # 行動の番号で箇所を指定できるよう、整形されない箇条書きにする
        lines.append(
            f"- **{index + 1}.** `{marker}` {names[step.actor]}: {tr.text(step.description)}"
        )
        # 引いた直後に手札の内容を出す。たねを出せない原因（引けない／出さない）を判別するため
        if "枚引く" in step.description:
            lines.append(f"  - 手札: {render_hand(list(step.hand[step.actor]), tr)}")
        # 終了時にも出す。使わずに残したカード（出せたのに出さなかったたね等）を見るため
        if step.description == "ターンを終える" and step.turn > 0:
            lines.append(f"  - 終了時の手札: {render_hand(list(step.hand[step.actor]), tr)}")
        # ダメージが動いた行動とポイントが動いた行動の後だけ盤面を出す。
        # 全行動で出すと長すぎる
        prev = replay.steps[index - 1].points if index > 0 else (0, 0)
        if step.points != prev or "ダメージ" in step.description:
            lines.append("")
            lines.append(f"  - 先攻の盤面: {render_board(step, 0, tr)}")
            lines.append(f"  - 後攻の盤面: {render_board(step, 1, tr)}")
            lines.append(f"  - ポイント: 先攻 {step.points[0]} - 後攻 {step.points[1]}")
            lines.append("")
    return lines


def write_seed_reports(
    args: argparse.Namespace,
    a: dict[str, Any],
    b: dict[str, Any],
    tr: Any,
    out_dir: Path,
    lines: list[str],
    seed: int,
    replay: Any,
    sim: float,
    actual: float | None,
    matches: int,
) -> None:
    """1 つのシードについて、Markdown・盤面つき HTML・AI 分析用を書き出す。"""
    lines.append(f"### シード {seed} の全行動")
    lines.append("")
    lines.append(
        "`先` は先攻、`後` は後攻。「効果:」はワザや特性の効果として deckgym が"
        "内部で処理した行動。ダメージやポイントが動いた行動の後に盤面を、"
        "引いた直後と番の終わりにその人の手札を示す。"
    )
    lines.extend(render_replay(replay, a["name"], b["name"], tr))

    path = out_dir / matchup_report_filename(args.strategy, seed)
    path.write_text(_tidy(lines), encoding="utf-8")
    print(f"{path} に書き出しました（{len(lines)} 行）")

    # 盤面つきの HTML。ブラウザで開くと、場と手札をカード画像で追える
    board_path = path.with_suffix(".html")
    board_path.write_text(
        render_replay_html(
            replay,
            (a["name"], b["name"]),
            out_dir,
            tr,
            f"{a['name']} 対 {b['name']}（方策 {args.strategy} / シード {seed}）",
            f"シミュレーション {sim:.1%}（{args.games} 試合）"
            + ("" if actual is None else f" / 実戦 {actual:.1%}（{matches} 試合）"),
        ),
        encoding="utf-8",
    )
    print(f"{board_path} に盤面つきの HTML を書き出しました")

    # 外部の AI に渡す用。ルール・カード辞書・記法を同梱した自己完結の Markdown
    ai_path = out_dir / path.name.replace("report", "ai-report", 1)
    ai_path.write_text(
        render_ai_report(
            replay,
            (a["name"], b["name"]),
            (a["decklists"][0], b["decklists"][0]),
            tr,
            strategy=args.strategy,
            seed=seed,
            games=args.games,
            simulated=sim,
            actual=actual,
            matches=matches,
            engine_revision=engine.deckgym_revision()[:7],
        ),
        encoding="utf-8",
    )
    print(f"{ai_path} に AI 分析用のレポートを書き出しました")


def _pick_deck(
    rows: list[dict[str, Any]],
    rank: int | None,
    path: Path | None,
    name: str | None,
    label: str,
) -> dict[str, Any]:
    """メタの順位か、デッキファイルのどちらかからデッキを選ぶ。

    メタ外のデッキ（ユーザー自身のデッキ）を診断できるようにするための入口。
    """
    if path is not None:
        text = path.read_text(encoding="utf-8")
        engine.validate_deck(text)
        return {
            "name": name or path.stem,
            "slug": path.stem,
            "decklists": [text],
            "matchups": [],
            "rank": 0,
        }
    if rank is None:
        raise SystemExit(
            f"デッキ {label} は --{label.lower()} か --deck-{label.lower()} で指定してください"
        )
    found = next((r for r in rows if r["rank"] == rank), None)
    if found is None:
        raise SystemExit(f"順位 {rank} のデッキが見つかりません")
    return found


def _tidy(lines: list[str]) -> str:
    """markdownlint に通る形に整える。

    空行が 2 行続くと MD012 に引っかかる（盤面の直後に節が始まると起きる）。
    生成物を手で直すことになるので、書き出す側で潰しておく。
    """
    tidied: list[str] = []
    for line in "\n".join(lines).split("\n"):
        if not line.strip() and tidied and not tidied[-1].strip():
            continue
        tidied.append(line)
    return "\n".join(tidied).rstrip("\n") + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--a", type=int, help="先攻側の順位（1 始まり）")
    parser.add_argument("--b", type=int, help="後攻側の順位（1 始まり）")
    parser.add_argument(
        "--deck-a",
        type=Path,
        help="先攻側にメタ外のデッキを使う。デッキファイルへのパス（--a の代わり）",
    )
    parser.add_argument(
        "--deck-b",
        type=Path,
        help="後攻側にメタ外のデッキを使う。デッキファイルへのパス（--b の代わり）",
    )
    parser.add_argument("--name-a", help="--deck-a の表示名（既定はファイル名）")
    parser.add_argument("--name-b", help="--deck-b の表示名（既定はファイル名）")
    parser.add_argument("--strategy", default="l", help="両者に使う方策コード")
    parser.add_argument("--games", type=int, default=200, help="勝率を出すための試合数")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--seeds",
        help="全行動ログを出すシード。カンマ区切りで複数指定できる（既定は --seed のみ）。"
        "勝率の計算は 1 回で済むので、同じ組の複数試合を見たいときに使う",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    rows = load(args.snapshot)
    tr = Translator.from_file()
    for row in rows:
        row["name"] = tr.text(row["name"])
    a = _pick_deck(rows, args.a, args.deck_a, args.name_a, "A")
    b = _pick_deck(rows, args.b, args.deck_b, args.name_b, "B")

    # 実戦の相性はメタ同士でしか取れない。片方でもメタ外なら比較対象がない
    if args.deck_a or args.deck_b:
        actual, matches = None, 0
    else:
        actual, matches = actual_win_rate(a, b)
    result = engine.evaluate_matchup(
        a["decklists"][0], b["decklists"][0], args.strategy, args.strategy, args.games, args.seed
    )

    lines: list[str] = []
    lines.append(f"# {a['name']} 対 {b['name']}")
    lines.append("")
    lines.append(f"方策 `{args.strategy}` / シード {args.seed} / 代表デッキリスト各 1 件")
    lines.append("")
    lines.append(f"**以下の勝率はすべて「{a['name']}」から見た値**。")
    lines.append(f"相手は「{b['name']}」。")
    lines.append("")
    lines.append("## 1. 実戦とシミュレーションの比較")
    lines.append("")
    lines.append(f"| 項目 | {a['name']} の勝率 |")
    lines.append("| --- | --- |")
    actual_text = "データなし" if actual is None else f"{actual:.1%}（実戦 {matches} 試合）"
    lines.append(f"| 実戦（limitless の相性表） | {actual_text} |")
    sim = result.overall.win_rate
    low, high = result.overall.confidence_interval_95 or (0.0, 0.0)
    interval = f"95% 区間 {low:.1%}〜{high:.1%}"
    lines.append(f"| シミュレーション | {sim:.1%}（{args.games} 試合、{interval}） |")
    if actual is not None and sim is not None:
        lines.append(f"| **ずれ（シミュレーション − 実戦）** | **{sim - actual:+.1%}** |")
    lines.append(f"| うち {a['name']} が先攻の試合 | {result.going_first.win_rate:.1%} |")
    lines.append(f"| うち {a['name']} が後攻の試合 | {result.going_second.win_rate:.1%} |")
    lines.append(
        f"| 内訳 | {a['name']} の勝ち {result.overall.wins} / 負け {result.overall.losses} "
        f"/ 引き分け {result.overall.ties} |"
    )
    lines.append("")

    lines.append("## 2. デッキリスト")
    lines.append("")
    matchup_out_dir = args.out_dir / matchup_dirname(a["slug"], b["slug"])
    for label, row in [("先攻", a), ("後攻", b)]:
        lines.append(f"### {label}: {row['name']}")
        lines.append("")
        if "share" in row:
            lines.append(f"使用率 {row['share']:.1%} / 実戦の総合勝率 {row['win_rate']:.1%}")
        else:
            lines.append("メタ環境外のデッキ（使用率と実戦の勝率はない）")
        lines.append("")
        lines.extend(render_decklist(row["decklists"][0], matchup_out_dir, tr))
        lines.append("")

    lines.append("## 3. 対戦の推移")
    lines.append("")
    lines.append(
        f"同じ条件で {SAMPLE_GAMES} 試合を回し、シードごとの結果を示す。先攻は常に {a['name']}。"
    )
    lines.append("")
    lines.append("| シード | 結果 | ターン数 | 行動数 | 最終ポイント |")
    lines.append("| ---: | --- | ---: | ---: | --- |")
    replays = []
    for seed in range(args.seed, args.seed + SAMPLE_GAMES):
        replay = engine.replay_game(
            a["decklists"][0], b["decklists"][0], args.strategy, args.strategy, seed
        )
        replays.append((seed, replay))
        winner = {"PlayerA": f"{a['name']} の勝ち", "PlayerB": f"{b['name']} の勝ち"}.get(
            replay.result, "引き分け"
        )
        lines.append(
            f"| {seed} | {winner} | {replay.turns} | {len(replay.steps)} "
            f"| {replay.points[0]} - {replay.points[1]} |"
        )
    lines.append("")

    out_dir = args.out_dir / matchup_dirname(a["slug"], b["slug"])
    out_dir.mkdir(parents=True, exist_ok=True)
    seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else [args.seed]
    for seed in seeds:
        replay = next(
            (r for s, r in replays if s == seed),
            None,
        ) or engine.replay_game(
            a["decklists"][0], b["decklists"][0], args.strategy, args.strategy, seed
        )
        write_seed_reports(args, a, b, tr, out_dir, list(lines), seed, replay, sim, actual, matches)


if __name__ == "__main__":
    main()
