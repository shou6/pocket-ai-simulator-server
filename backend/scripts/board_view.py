"""対戦ログを、対話的に見られる 1 枚の HTML に書き出す。

テキストだけでは何が場に出ているのか掴みにくい。カード画像で盤面を並べ、
スライダーとキーボードで手を 1 つずつ進めながら追えるようにする。

レイアウトは Claude Design のプロジェクト「ポケポケ対戦リプレイUI」の
`Replay Board.dc.html` を、外部の依存なしの素の HTML/CSS/JS として実装したもの。
デザイントークン（色・書体・blueprint の枠）は同プロジェクトの `styles.css` に合わせている。

盤面は `data/raw/flibustier/` に展開したカード画像を相対パスで参照するので、
書き出した HTML はローカルのブラウザで開く（画像は git 管理していない）。

他のシミュレーションからも使えるよう、`render_replay_html()` は
`pocket_engine_py.replay_game()` の戻り値をそのまま受け取る。
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_DIR = REPO_ROOT / "data" / "raw" / "flibustier" / "dist" / "images" / "cards-by-set"

# エネルギーの色。ログの日本語名（`replay.rs` の `energy_name`）に合わせる
ENERGY_COLORS = {
    "草": "#3fa34d",
    "炎": "#e4572e",
    "水": "#3d8bd4",
    "雷": "#e5b800",
    "超": "#9b5bb5",
    "闘": "#b5651d",
    "悪": "#4b4b5a",
    "鋼": "#8d9aa5",
    "竜": "#c9a227",
    "無": "#a8a8a8",
}


def image_src(card_id: str, out_dir: Path) -> str | None:
    """カード画像の相対パス。画像が無ければ None。"""
    set_code, _, number = card_id.partition(" ")
    path = IMAGE_DIR / set_code.replace("P-", "PROMO-") / f"{int(number)}.webp"
    if not path.exists():
        return None
    return Path(os.path.relpath(path, out_dir)).as_posix()


def _card(card_ref: Any, out_dir: Path, tr: Any) -> dict[str, Any]:
    """手札・初手のカード 1 枚。"""
    return {
        "img": image_src(card_ref.id, out_dir),
        "name": tr.card_name(card_ref.name),
        "hp": None,
        "energy": [],
        "status": [],
        "tool": [],
    }


def _pokemon(pokemon: Any, out_dir: Path, tr: Any) -> dict[str, Any]:
    """場のポケモン 1 匹。"""
    return {
        "img": image_src(pokemon.id, out_dir),
        "name": tr.card_name(pokemon.name),
        "hp": [pokemon.remaining_hp, pokemon.max_hp],
        "energy": [{"label": e, "color": ENERGY_COLORS.get(e, "#7a7a7d")} for e in pokemon.energy],
        "status": list(pokemon.status),
        "tool": [tr.card_name(tool.name) for tool in pokemon.tools],
    }


def _side(step: Any, player: int, name: str, out_dir: Path, tr: Any) -> dict[str, Any]:
    active = step.active[player]
    return {
        "name": name,
        "points": step.points[player],
        "hand": step.hand_size[player],
        "deck": step.deck_size[player],
        "active": _pokemon(active, out_dir, tr) if active is not None else None,
        "bench": [_pokemon(p, out_dir, tr) for p in step.bench[player]],
    }


def _board(step: Any, names: tuple[str, str], out_dir: Path, tr: Any) -> dict[str, Any]:
    stadium = None
    if step.stadium is not None:
        stadium = {
            "name": tr.card_name(step.stadium.name),
            "img": image_src(step.stadium.id, out_dir),
            # 自分（player 0）が出したものは左、相手のものは右に置く
            "mine": step.stadium_owner == 0,
        }
    side_label = "自分" if step.actor == 0 else "相手"
    hand = [_card(c, out_dir, tr) for c in step.hand[step.actor]]
    return {
        "me": _side(step, 0, names[0], out_dir, tr),
        "opp": _side(step, 1, names[1], out_dir, tr),
        "stadium": stadium,
        "handLabel": f"{side_label}の手札（{len(hand)} 枚）" if hand else "",
        "hand": hand,
    }


def build_replay_data(
    replay: Any,
    names: tuple[str, str],
    out_dir: Path,
    tr: Any,
    title: str,
    meta: str = "",
) -> dict[str, Any]:
    """HTML に埋め込む対戦データ。他の描画（別の UI）からも使える形にしておく。"""
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(replay.steps):
        entry: dict[str, Any] = {
            "no": index + 1,
            "turn": step.turn,
            "side": "first" if step.actor == 0 else "second",
            "text": tr.text(step.description),
            "tag": "先" if step.actor == 0 else "後",
        }
        if "枚引く" in step.description:
            entry["handList"] = [tr.card_name(c.name) for c in step.hand[step.actor]]
        # 行動 1 つごとに盤面を残す。「ベンチ1に出す」の直後にその 1 匹だけが増えた盤面を
        # 見られるようにする（ユーザーの要望）。以前は番の終わりとダメージの場面だけだった
        entry["board"] = _board(step, names, out_dir, tr)
        steps.append(entry)
    opening = [
        {
            "side": label,
            "deck": names[player],
            "cards": [_card(c, out_dir, tr) for c in replay.opening_hands[player]],
        }
        for player, label in ((0, "先攻"), (1, "後攻"))
    ]
    return {
        "title": title,
        "meta": meta,
        "meSide": "first",
        "opening": opening,
        "steps": steps,
    }


ASSET_DIR = Path(__file__).resolve().parent
STYLE = (ASSET_DIR / "board_view.css").read_text(encoding="utf-8")
"""盤面ビューのスタイル。Claude Design の `styles.css` のトークンに合わせてある。"""

SCRIPT = (ASSET_DIR / "board_view.js").read_text(encoding="utf-8")
"""盤面ビューの操作（スライダー・再生・差分の強調）。外部の依存はない。"""


def render_replay_html(
    replay: Any,
    names: tuple[str, str],
    out_dir: Path,
    tr: Any,
    title: str,
    summary: str = "",
) -> str:
    """対戦ログ 1 試合を、対話的に見られる 1 枚の HTML にする。"""
    data = build_replay_data(replay, names, out_dir, tr, title, summary)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    # 勝敗はエンジンの判定をそのまま使う。ポイントが 3 に届かなくても、
    # 相手の場のポケモンが尽きれば勝ちになる（2-0 の試合を引き分けと誤表示していた）
    winner = {"PlayerA": "自分", "PlayerB": "相手"}.get(replay.result, "引き分け")
    score = f"{replay.points[0]}–{replay.points[1]}"
    result = f"{winner} 勝利 {score}" if winner != "引き分け" else f"引き分け {score}"
    corners = (
        '<i class="corner tl"></i><i class="corner tr"></i>'
        '<i class="corner bl"></i><i class="corner br"></i>'
    )
    labels = {"先攻": "自分", "後攻": "相手"}
    opening = "".join(
        f'<div class="tr-row" style="cursor:default">'
        f'<span class="num">{labels[o["side"]]}</span>'
        f'<span style="font-size:10px;color:var(--color-neutral-600)">初手</span>'
        f'<span style="min-width:0">{html.escape("、".join(c["name"] for c in o["cards"]))}</span>'
        f"</div>"
        for o in data["opening"]
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head><body>
<div class="app">

  <div class="head">
    <div class="head-main">
      <div class="kicker">BATTLE REPLAY / 対戦リプレイ</div>
      <div class="head-title">{html.escape(title)}</div>
      <div class="head-meta">{html.escape(summary)}</div>
    </div>
    <div style="display:flex;gap:8px;align-items:stretch">
      <div class="blueprint stat">{corners}
        <div class="stat-label">結果</div>
        <div class="stat-value">{html.escape(result)}</div>
      </div>
      <div class="blueprint stat">{corners}
        <div class="stat-label">決着 / 総アクション</div>
        <div class="stat-value">T{data["steps"][-1]["turn"]} · {len(data["steps"])}手</div>
      </div>
    </div>
  </div>

  <div class="summary" id="summary">
    <div class="blueprint panel" style="flex:2 1 380px">{corners}
      <div class="panel-head">
        <div class="panel-title">盤面HP残量とポイント獲得</div>
        <div class="panel-note">■ 自分 ／ □ 相手 ・ ▲ = ポイント</div>
      </div>
      <svg id="graph" viewBox="0 0 640 150" class="graph"></svg>
    </div>
    <div class="blueprint panel" style="flex:1 1 260px">{corners}
      <div class="panel-title">ターン別イベント</div>
      <div class="scroll-list" id="events"></div>
    </div>
    <div class="blueprint panel" style="flex:1 1 230px">{corners}
      <div class="panel-title">初手・進化到達とトレーナーズ</div>
      <div class="scroll-list" id="trainers-wrap" style="gap:2px">
        {opening}
        <div id="trainers" style="display:flex;flex-direction:column;gap:2px"></div>
      </div>
    </div>
  </div>

  <div class="main">
    <div class="blueprint log-panel">{corners}
      <div class="log-head">
        <div class="panel-title">全アクション</div>
        <div class="panel-note" id="curNoLabel"></div>
      </div>
      <div class="log-body" id="log"></div>
    </div>

    <div class="blueprint board-panel">{corners}
      <div class="turn-banner" id="turnBanner">
        <span class="turn-who" id="turnWho"></span>
        <span class="turn-no" id="turnNo"></span>
      </div>
      <div class="board-head">
        <div class="board-title" id="boardTitle"></div>
        <div class="panel-note" id="boardMeta"></div>
      </div>
      <div id="boardBody" style="display:flex;flex-direction:column;gap:8px"></div>
    </div>
  </div>

  <div class="controls">
    <div style="display:flex;gap:6px;align-items:center">
      <button class="btn btn-secondary" id="prev" style="min-width:38px">◀</button>
      <button class="btn btn-primary" id="playBtn" style="min-width:62px">▶ 再生</button>
      <button class="btn btn-secondary" id="next" style="min-width:38px">▶</button>
    </div>
    <div class="slider-wrap">
      <input type="range" id="slider" min="0" max="0" step="1" value="0">
      <div class="slider-legend">
        <span id="pos"></span>
        <span>← → で前後 · Space で再生 · 行クリックでジャンプ</span>
      </div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;font-size:12px">
      <span class="muted">移動単位</span>
      <div class="seg">
        <button class="seg-opt on" id="unitAction">アクション</button>
        <button class="seg-opt" id="unitTurn">手番</button>
      </div>
      <button class="btn btn-ghost" id="summaryBtn">サマリーを隠す</button>
      <button class="btn btn-ghost" id="handBtn">手札を隠す</button>
    </div>
  </div>

</div>
<script>window.REPLAY_DATA = {payload};</script>
<script>{SCRIPT}</script>
</body></html>
"""
