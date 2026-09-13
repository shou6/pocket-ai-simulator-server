"""デッキ最適化の CLI。

`docs/requirements.md` の 3 つの提案モードを扱う。

- 環境全体に強いデッキ（F-05）：使用率で重み付けした期待勝率を最大化する
- 相手デッキを指定（F-03）：`--target` でその 1 デッキへの勝率を最大化する
- 所持カードで最良（F-04）：`--owned` で持っている範囲に絞る

    uv run python -m pocket_api.optimize.cli --games 100 --generations 5
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pocket_api.ingest.snapshot import latest_snapshot_path
from pocket_api.optimize.evaluate import Evaluation
from pocket_api.optimize.propose import (
    DEFAULT_STORE,
    MetaDeck,
    SearchSettings,
    build_opponents,
    independent_evaluation,
    propose,
)
from pocket_api.optimize.recipe import DeckRecipe, Ownership

REPO_ROOT = Path(__file__).resolve().parents[4]
# 既定は data/meta のいちばん新しいスナップショット（メタを更新したら自動で追従する）
DEFAULT_SNAPSHOT = latest_snapshot_path(REPO_ROOT / "data" / "meta")

__all__ = [
    "MetaDeck",
    "build_opponents",
    "independent_evaluation",
    "load_meta",
    "load_ownership",
    "render_candidates",
    "render_result",
]


def load_meta(snapshot: Path) -> list[MetaDeck]:
    """メタのスナップショットからアーキタイプを読む。代表デッキは先頭のリストを使う。"""
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    rows = data.get("archetypes") or data.get("decks") or []
    return [
        MetaDeck(name=row["name"], share=float(row["share"]), decklist=row["decklists"][0])
        for row in rows
        if row.get("decklists")
    ]


def render_result(recipe: DeckRecipe, evaluation: Evaluation, decks: list[MetaDeck]) -> str:
    """提案されたデッキと勝率を、そのまま読める形にする。"""
    names = {d.name: d.share for d in decks}
    lines = [
        f"期待勝率 {evaluation.win_rate:.1%}（1 組あたり {evaluation.games} 試合）",
        "",
        "## 相手ごとの勝率",
        "",
        "| 相手 | 使用率 | 勝率 |",
        "| --- | ---: | ---: |",
    ]
    for name, rate in sorted(evaluation.per_opponent, key=lambda x: -x[1]):
        share = names.get(name)
        share_text = "-" if share is None else f"{share:.1%}"
        lines.append(f"| {name} | {share_text} | {rate:.1%} |")
    lines += ["", "## デッキリスト", "", "```text", recipe.to_text().strip(), "```"]
    return "\n".join(lines)


def render_candidates(others: Sequence[tuple[DeckRecipe, float]]) -> str:
    """次点の候補を、勝率順にデッキリストつきで並べる（F-03）。

    最良の 1 件しか出さないと、僅差の別案があっても見えない。評価ぶれ（±7%）に
    収まる差なら「どれを使ってもよい」という情報のほうが役に立つ。
    """
    if not others:
        return ""
    lines = ["", "## 次点の候補", ""]
    for rank, (recipe, rate) in enumerate(others, start=2):
        lines += [
            f"### {rank} 位（期待勝率 {rate:.1%}）",
            "",
            "```text",
            recipe.to_text().strip(),
            "```",
            "",
        ]
    return "\n".join(lines)


def load_ownership(path: Path | None) -> Ownership | None:
    """所持カードを読む。`{"A1 042": 2}` の形の JSON。"""
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Ownership({str(k): int(v) for k, v in data.items()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--target", help="この 1 デッキへの勝率を最大化する（既定は環境全体）")
    parser.add_argument("--owned", type=Path, help="所持カードの JSON。持っている範囲に絞る")
    # 既定は校正済みの l。p は実戦より 11 ポイント高く見積もる（バタフリーで 64.6% 対 53.2%）
    parser.add_argument("--strategy", default="l", help="探索で使う方策（既定は校正済みの l）")
    parser.add_argument("--games", type=int, default=60, help="1 組あたりの試合数")
    parser.add_argument("--population", type=int, default=12)
    parser.add_argument("--generations", type=int, default=4)
    parser.add_argument("--climb-steps", type=int, default=3, help="仕上げの山登りの回数")
    parser.add_argument(
        "--neighbours",
        type=int,
        default=40,
        help="山登りで一度に見る候補の数。モデルが無いときだけ効く（実評価が候補の数だけ掛かるため）",
    )
    parser.add_argument(
        "--similar-width",
        type=int,
        default=16,
        help="1 枚を入れ替えるとき、役割の近い何枚を候補にするか",
    )
    parser.add_argument(
        "--pool",
        choices=("meta", "all"),
        default="all",
        help="探索に使う札。meta はメタデッキの札だけ、all は同じタイプの実装済みカード全部",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="出す候補の数。2 件目以降は次点としてデッキリストを添える",
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--report-games",
        type=int,
        default=200,
        help="成績を測り直すときの試合数。探索とは別の試合で測る（既定は校正済みの 200）",
    )
    parser.add_argument("--out", type=Path, help="結果の書き出し先（既定は標準出力）")
    parser.add_argument(
        "--surrogate",
        type=Path,
        default=DEFAULT_STORE,
        help="サロゲートモデルの学習データ。あれば予測で候補を絞る",
    )
    parser.add_argument(
        "--no-surrogate",
        action="store_true",
        help="モデルを使わず、すべての候補を実評価する",
    )
    parser.add_argument(
        "--screen-keep",
        type=int,
        default=20,
        help="山登りで、予測の上位いくつを実評価するか。8 では当たりを取りこぼす",
    )
    args = parser.parse_args(argv)

    decks = load_meta(args.snapshot)
    settings = SearchSettings(
        strategy=args.strategy,
        games=args.games,
        report_games=args.report_games,
        population=args.population,
        generations=args.generations,
        climb_steps=args.climb_steps,
        neighbours=args.neighbours,
        similar_width=args.similar_width,
        screen_keep=args.screen_keep,
        pool=args.pool,
        top=args.top,
        seed=args.seed,
        store=args.surrogate,
        use_surrogate=not args.no_surrogate,
    )
    try:
        report = propose(
            decks,
            settings=settings,
            target=args.target,
            ownership=load_ownership(args.owned),
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"サロゲートモデル: {report.model_note}", file=sys.stderr)
    if report.samples_written:
        print(
            f"学習データに {report.samples_written} 件を足しました（{args.surrogate}）",
            file=sys.stderr,
        )

    best_recipe, evaluation = report.ranked[0]
    header = [
        f"# デッキ提案（方策 {args.strategy} / 探索 {args.games} 試合 / シード {args.seed}）",
        "",
        f"対象: {args.target or '環境全体（使用率で重み付け）'}",
        f"探索: 個体 {args.population} × 世代 {args.generations} + 山登り {report.climb_steps} 手"
        f"（札 {report.pool_size} 種）",
        f"評価した対戦 {report.evaluated_matchups} 組（キャッシュ命中 {report.cache_hits} 回）"
        f" / {report.elapsed_seconds:.1f} 秒",
        "",
        f"成績は探索とは**別の試合**で測り直した（1 組 {args.report_games} 試合）。"
        f"探索中の値は {report.searched_win_rate:.1%} だったが、これは何百もの候補から"
        "「その試合数で最も高く出たもの」を選んだ値で、実力より高く出る。",
        "",
    ]
    others = [(recipe, value.win_rate) for recipe, value in report.ranked[1:]]
    text = (
        "\n".join(header)
        + render_result(best_recipe, evaluation, decks)
        + "\n"
        + render_candidates(others)
    )
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"{args.out} に書き出しました")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
