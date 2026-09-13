"""対戦ログを、外部の AI に分析させるための自己完結した Markdown にする。

`report.md` は devcontainer で開発者が読む前提（カード画像つき、和名、前提は既知）だが、
外部の AI にはその前提がない。ここでは次を 1 ファイルに詰める。

- 何の文書で、何を分析してほしいのかを先頭の frontmatter と概要に書く
- ゲームのルールのうち、判断に必要なものを明記する（本家 TCG との違いを含む）
- 出てくるカードの性能（HP・ワザ・コスト・効果・特性）を辞書として添える
- 記法（ターンの数え方、行動の書式、盤面の書式）を定義する
- 全行動を、機械的に読める 1 行 1 行動の書式で並べる

画像は参照できないので入れない。カード名は英名（和名を併記）で統一する。
"""

from __future__ import annotations

import re
from typing import Any

import pocket_engine_py as engine

# デッキリストの行: 「2 Caterpie B3b 1」
_DECK_LINE_RE = re.compile(r"^(\d+)\s+(.+?)\s+(P-[AB]|[AB]\d[a-z]?)\s+(\d+)\s*$")

RULES = """\
## 2. ルール（判断に必要な範囲）

Pokémon TCG Pocket は本家の Pokémon TCG とは別のルールで、次の点が特に異なる。

- **勝利条件**: 先に 3 ポイント取ったほうが勝ち。相手をきぜつさせると、たね・進化ポケモンは 1 点、
  ポケモン ex は 2 点、メガシンカ ex は 3 点が入る。
  つまりメガシンカ ex を 1 体倒されると即敗北になる。
- **エネルギー**: 山札から引くのではなく、毎ターン「エネルギーゾーン」から 1 個だけ供給され、
  好きなポケモンに 1 個付けられる。**先攻の最初の番だけは供給されない**
  （先攻はエネルギーが 1 ターン遅い）。
- **ベンチ**: 3 匹まで。手札からたねポケモンを何匹でも出せる。
- **サポート**: 1 ターンに 1 枚。グッズは何枚でも使える。
- **進化**: 場に出した番と、進化させた番には進化できない。
  「ふしぎなアメ」でたねから 2 進化へ飛べる。
- **にげる**: 付いているエネルギーをコストぶん捨ててベンチと入れ替える。1 ターンに 1 回。
- **弱点**: 弱点タイプのワザを受けると +20 ダメージ。抵抗力は無い。
- **ポケモンチェック**: お互いの番の終わりに、どく 10・やけど 20 のダメージが入る。
  やけどとねむりはコインで回復判定。ねむり・まひの間はワザとにげるが使えない。
- **その他**: 手札の上限 10 枚、山札切れでは負けない、30 ターンで引き分け、こんらんの自傷は無い。
"""

NOTATION = """\
## 4. ログの記法

- `T{n}` はターン番号。ターン 0 は準備（たねをバトル場とベンチに出すだけ）。
  ターン 1 から先攻の番、ターン 2 が後攻の番、以降交互。**片方の番ごとに 1 ずつ増える**。
- `[先]` は先攻プレイヤー、`[後]` は後攻プレイヤーの行動。
- 行の先頭の数字は行動の通し番号。分析で場所を指すときはこの番号を使ってほしい。
- `効果:` で始まる行は、ワザ・特性・トレーナーズの効果としてルールエンジンが分けて処理した行動。
  たとえばワザ本体が「0 ダメージ」でも、その直後の `効果:` の行でベンチにダメージが入る。
- `手札:` はその時点の手札（引いた直後と番の終わりに出す）。相手の手札は見えない前提だが、
  このログには両者ぶんが記録されている。
- `盤面:` はその行動の直後の状態。
  `名前 残りHP/最大HP E:付いているエネルギー [状態異常] {どうぐ}` の形式。
  `ポイント` は「先攻 - 後攻」。
"""


def collect_card_ids(decklists: list[str]) -> list[str]:
    """デッキリストに出てくるカードの識別子を、出た順に集める。"""
    ids: list[str] = []
    for decklist in decklists:
        for raw in decklist.splitlines():
            match = _DECK_LINE_RE.match(raw.strip())
            if match is None:
                continue
            _, _, set_code, number = match.groups()
            card_id = f"{set_code} {int(number):03d}"
            if card_id not in ids:
                ids.append(card_id)
    return ids


def render_card_glossary(ids: list[str], tr: Any) -> list[str]:
    """カードの性能を表にする。外部 AI はこのセットのカードを知らない。"""
    lines = [
        "## 3. カード辞書",
        "",
        "このログに出てくるカードの性能。効果文は英語の原文（ルールエンジンが解釈する文言）。",
        "",
        "### 3.1 ポケモン",
        "",
        "| カード | 和名 | タイプ | HP | 段階 | 進化元 | 弱点 | にげる | ワザ / 特性 |",
        "| --- | --- | --- | ---: | --- | --- | --- | ---: | --- |",
    ]
    trainers = []
    for card in engine.card_details(ids):
        if card.kind != "ポケモン":
            trainers.append(card)
            continue
        moves = []
        if card.ability is not None:
            moves.append(f"特性【{card.ability.title}】{card.ability.effect}")
        for attack in card.attacks:
            cost = "".join(attack.cost) or "なし"
            effect = f" / {attack.effect}" if attack.effect else ""
            moves.append(f"ワザ【{attack.title}】{cost} → {attack.damage}{effect}")
        stage = {0: "たね", 1: "1 進化", 2: "2 進化"}.get(card.stage or 0, "?")
        lines.append(
            f"| {card.name}（{card.id}） | {tr.card_name(card.name)} | {card.energy_type} "
            f"| {card.hp} | {stage} | {card.evolves_from or '-'} | {card.weakness or '-'} "
            f"| {card.retreat_cost} | {'<br>'.join(moves) or '-'} |"
        )
    lines += [
        "",
        "### 3.2 トレーナーズ",
        "",
        "| カード | 和名 | 種類 | 効果 |",
        "| --- | --- | --- | --- |",
    ]
    for card in trainers:
        lines.append(
            f"| {card.name}（{card.id}） | {tr.card_name(card.name)} | {card.kind} "
            f"| {card.effect or '-'} |"
        )
    return lines


def render_decklists(names: tuple[str, str], decklists: tuple[str, str]) -> list[str]:
    lines = ["## 5. デッキリスト", ""]
    sides = (("先攻", names[0]), ("後攻", names[1]))
    for (label, name), decklist in zip(sides, decklists, strict=True):
        lines += [f"### {label}: {name}", "", "```text", decklist.strip(), "```", ""]
    return lines


def _pokemon_text(pokemon: Any) -> str:
    energy = "".join(pokemon.energy) or "-"
    status = f" [{'/'.join(pokemon.status)}]" if pokemon.status else ""
    tool = "".join(f" {{{t.name}}}" for t in pokemon.tools)
    return f"{pokemon.name} {pokemon.remaining_hp}/{pokemon.max_hp} E:{energy}{status}{tool}"


def _board_text(step: Any) -> list[str]:
    lines = []
    for player, label in ((0, "先攻"), (1, "後攻")):
        active = step.active[player]
        head = _pokemon_text(active) if active is not None else "（バトル場は空）"
        bench = "、".join(_pokemon_text(p) for p in step.bench[player]) or "なし"
        lines.append(
            f"    盤面 {label}: バトル場 {head} / ベンチ {bench} "
            f"/ 手札 {step.hand_size[player]}枚 / 山札 {step.deck_size[player]}枚"
        )
    stadium = "なし"
    if step.stadium is not None:
        owner = "先攻" if step.stadium_owner == 0 else "後攻"
        stadium = f"{step.stadium.name}（{owner}が出した）"
    lines.append(
        f"    スタジアム: {stadium} / ポイント: 先攻 {step.points[0]} - 後攻 {step.points[1]}"
    )
    return lines


def render_actions(replay: Any) -> list[str]:
    """全行動。1 行 1 行動で、盤面と手札は字下げした続きの行に置く。"""
    lines = ["## 6. 全行動", "", "```text"]
    for player, label in ((0, "先攻"), (1, "後攻")):
        hand = "、".join(c.name for c in replay.opening_hands[player]) or "なし"
        lines.append(f"{label}の初手: {hand}")
    lines.append("")
    for index, step in enumerate(replay.steps):
        who = "先" if step.actor == 0 else "後"
        lines.append(f"{index + 1:>3}. T{step.turn} [{who}] {step.description}")
        if "枚引く" in step.description or step.description == "ターンを終える":
            hand = "、".join(c.name for c in step.hand[step.actor]) or "なし"
            lines.append(f"    手札: {hand}")
        previous = replay.steps[index - 1] if index > 0 else None
        moved = previous is None or step.points != previous.points
        if moved or "ダメージ" in step.description or "回復" in step.description:
            lines += _board_text(step)
    lines += ["```", ""]
    return lines


def render_ai_report(
    replay: Any,
    names: tuple[str, str],
    decklists: tuple[str, str],
    tr: Any,
    *,
    strategy: str,
    seed: int,
    engine_revision: str,
    games: int | None = None,
    simulated: float | None = None,
    actual: float | None = None,
    matches: int = 0,
    focus: str = "",
) -> str:
    """外部 AI に渡す、自己完結した対戦ログ。

    `simulated` を渡すと、実戦とのずれを「なぜ見てほしいのか」として書く。
    `focus` は個別の試合を渡すときの補足（「主役が早く立ったのに負けた」など）。
    """
    lines = [
        "---",
        "document_type: pokemon_tcg_pocket_battle_log",
        "purpose: 外部の AI に、シミュレーションの立ち回りの誤りを指摘してもらう",
        "generated_by: pocketAiSimulator",
        f"rules_engine: deckgym-core {engine_revision}",
        f"policy: {strategy}",
        f'matchup: "{names[0]} (先攻) vs {names[1]} (後攻)"',
        f"seed: {seed}",
        "simulated_win_rate_first_player: " + ("null" if simulated is None else f"{simulated:.3f}"),
        "actual_win_rate_first_player: " + ("null" if actual is None else f"{actual:.3f}"),
        "language: ja",
        "card_names: english (和名を併記)",
        "---",
        "",
        f"# {names[0]}（先攻）対 {names[1]}（後攻）",
        "",
        "## 1. この文書について",
        "",
        "ポケモンカードゲーム Pocket の対戦を、ルールエンジン（deckgym-core）と自作の方策で",
        "シミュレーションした 1 試合の全行動ログ。カードの性能・ルール・記法を同梱しているので、",
        "この文書だけで内容を追える。画像は含まない。",
        "",
        "**分析してほしいこと**: この試合で、先攻・後攻それぞれのプレイヤーが打った手のうち、",
        "人間の上級者なら選ばないであろう手を指摘してほしい。指摘には行動の通し番号を添えてほしい。",
        "特に、エネルギーの付け先、進化の順序、にげる判断、トレーナーズの使用順序、",
        "ワザを撃つ相手の選択に注目してほしい。",
        "",
    ]
    if focus:
        lines += [f"**この試合を選んだ理由**: {focus}", ""]
    if simulated is not None:
        gap = "不明" if actual is None else f"{simulated - actual:+.1%}"
        actual_text = "データなし" if actual is None else f"{actual:.1%}（{matches} 試合）"
        lines += [
            "**背景**: このシミュレーションの勝率は、実戦（大会結果）の勝率とずれている。",
            "",
            f"| 項目 | 先攻（{names[0]}）から見た勝率 |",
            "| --- | --- |",
            f"| 実戦（大会の集計） | {actual_text} |",
            f"| シミュレーション | {simulated:.1%}（{games} 試合） |",
            f"| ずれ | {gap} |",
            "",
            "ずれの原因が方策（打ち回し）にあるのか、別の要因なのかを判断する材料にしてほしい。",
            "",
        ]
    lines += [RULES, ""]
    lines += render_card_glossary(collect_card_ids(list(decklists)), tr)
    lines += ["", NOTATION, ""]
    lines += render_decklists(names, decklists)
    lines += render_actions(replay)
    winner = {"PlayerA": "先攻", "PlayerB": "後攻"}.get(replay.result, "引き分け")
    lines += [
        "## 7. 結果",
        "",
        f"- 決着: {replay.turns} ターン",
        f"- 最終ポイント: 先攻 {replay.points[0]} - 後攻 {replay.points[1]}",
        f"- 勝者: {winner}",
        "",
    ]
    return _tidy(lines)


def _tidy(lines: list[str]) -> str:
    """節を連結した結果を整える。

    節ごとに末尾へ空行を足すので、繋ぐと空行が重なる。markdownlint（MD012）に
    合わせて 1 行にまとめ、末尾の空行も落とす。
    """
    # 要素そのものが複数行を含むことがあるので、連結してから 1 行ずつ見る
    tidied: list[str] = []
    for line in "\n".join(lines).split("\n"):
        if not line.strip() and tidied and not tidied[-1].strip():
            continue
        tidied.append(line)
    return "\n".join(tidied).rstrip("\n") + "\n"
