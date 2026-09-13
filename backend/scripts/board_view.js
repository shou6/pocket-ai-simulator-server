const D = window.REPLAY_DATA;
const STEPS = D.steps;
/** 手前（自分＝シミュレーション対象）がどちらの手番か。エンジンは player 0 を先攻に固定している。 */
const ME_SIDE = D.meSide || "first";
const ME_ORDER = ME_SIDE === "first" ? "先攻" : "後攻";
const OPP_ORDER = ME_SIDE === "first" ? "後攻" : "先攻";
const isMine = (side) => side === ME_SIDE;
const state = { idx: 0, unit: "action", playing: false, summary: true, hand: true, timer: null, turnKey: null };
const el = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function hpColor(pct) {
  if (pct <= 33) return "var(--color-danger)";
  if (pct <= 66) return "var(--color-warn)";
  return "var(--color-accent-600)";
}

/** 直前の盤面と比べて、各枠に何が起きたか（進化・被弾・KO など）を出す。 */
function diffSide(cur, prev) {
  const out = {};
  if (!cur) return out;
  const key = (c) => c.name + "/" + (c.hp ? c.hp[1] : 0);
  const flatten = (s) =>
    s ? [].concat(s.active ? [{ c: s.active, loc: "A" }] : [], (s.bench || []).map((c, i) => ({ c, loc: "B" + i }))) : [];
  const cl = flatten(cur), pl = flatten(prev);
  if (!prev) { cl.forEach((x) => { out[x.loc] = { delta: "初期", color: "var(--color-accent)" }; }); return out; }
  const used = [];
  cl.forEach((x) => {
    let j = pl.findIndex((p, i) => !used[i] && p.loc === x.loc && key(p.c) === key(x.c));
    if (j < 0) j = pl.findIndex((p, i) => !used[i] && key(p.c) === key(x.c));
    if (j >= 0) { used[j] = 1; x.p = pl[j]; }
  });
  cl.forEach((x) => {
    if (x.p) return;
    const j = pl.findIndex((p, i) => !used[i] && p.loc === x.loc);
    if (j >= 0) { used[j] = 1; x.p = pl[j]; x.evo = true; }
  });
  const lost = pl.filter((p, i) => !used[i]);
  cl.forEach((x) => {
    let d = null;
    if (!x.p) d = { delta: "新規", color: "var(--color-accent)" };
    else if (x.evo) d = { delta: "進化", color: "var(--color-accent-700)" };
    else if (x.p.c.hp && x.c.hp && x.p.c.hp[0] !== x.c.hp[0]) {
      const n = x.c.hp[0] - x.p.c.hp[0];
      d = {
        delta: (n > 0 ? "+" : "") + n,
        color: n < 0 ? "var(--color-danger)" : "var(--color-accent)",
        tint: n < 0 ? "rgba(179,38,30,.07)" : "var(--color-accent-100)",
      };
    } else if (x.p.c.energy.length !== x.c.energy.length) {
      const n = x.c.energy.length - x.p.c.energy.length;
      d = { delta: "エネ" + (n > 0 ? "+" : "") + n, color: "var(--color-accent)" };
    } else if (x.p.loc[0] !== x.loc[0]) {
      d = { delta: x.loc === "A" ? "→バトル場" : "→ベンチ", color: "var(--color-accent-700)" };
    }
    if (d) out[x.loc] = d;
  });
  if (lost.some((p) => p.loc === "A")) {
    out.A = cl.some((x) => x.loc === "A")
      ? { delta: "KO→交代", color: "var(--color-danger)", tint: "rgba(179,38,30,.07)" }
      : { delta: "KO", color: "var(--color-danger)" };
  }
  return out;
}

function thumb(card, ph) {
  const name = esc(card ? card.name : "空");
  const img = card && card.img
    ? '<img src="' + esc(card.img) + '" alt="' + name + '" loading="lazy" onerror="this.style.display=\'none\'">'
    : "";
  return '<div class="thumb"><span class="ph">' + name + "</span>" + img + "</div>";
}

function marks(card) {
  if (!card) return "";
  const en = (card.energy || [])
    .map((e) => '<span class="en" style="background:' + esc(e.color) + '">' + esc(e.label) + "</span>")
    .join("");
  const bg = [].concat(card.status || [], card.tool || [])
    .map((b) => '<span class="bg">' + esc(b) + "</span>")
    .join("");
  return '<div class="marks">' + en + bg + "</div>";
}

/** HP バー。どうぐ（リーフマント等）で印刷 HP を超えた分は、枠から出さず薄い色で示す。 */
function hpBar(hp) {
  const cur = hp[0], max = hp[1];
  if (!max) return '<div class="bar"></div>';
  if (cur > max) {
    // 最大 HP が増えている。バー全体をボーナス色で塗り、印刷 HP のぶんを左から重ねる
    const printed = Math.round((max / cur) * 100);
    return (
      '<div class="bar"><i class="hp-bonus"></i>' +
      '<i style="width:' + printed + "%;background:" + hpColor(100) + '"></i></div>'
    );
  }
  const pct = Math.round((cur / max) * 100);
  return '<div class="bar"><i style="width:' + pct + "%;background:" + hpColor(pct) + '"></i></div>';
}

function slotHtml(card, dif, isActive, kicker) {
  const hp = (card && card.hp) || [0, 0];
  const d = dif || null;
  const delta = d && d.delta ? d.delta : "";
  const color = (d && d.color) || "var(--color-danger)";
  const border = delta ? color : "var(--color-neutral-300)";
  const bg = delta ? (d.tint || "var(--color-accent-100)") : "transparent";
  const name = card ? card.name : (delta === "KO" ? "空 (KO)" : "空");
  const tag = delta ? '<span class="delta" style="background:' + esc(color) + '">' + esc(delta) + "</span>" : "";
  const bar = hpBar(hp);
  if (isActive) {
    return (
      '<div class="active-slot" style="border-color:' + esc(border) + ";background:" + esc(bg) + '">' +
      thumb(card) +
      '<div class="body">' +
        '<div class="active-kicker" style="color:' + (kicker === "自分" ? "var(--color-accent-700)" : "var(--color-neutral-700)") + '">' + esc(kicker) + " バトル場</div>" +
        '<div class="nm">' + esc(name) + "</div>" +
        '<div class="active-hp"><b>' + (card ? hp[0] : "—") + "</b><span>/ " + (card ? hp[1] : "—") + "</span></div>" +
        bar + marks(card) +
      "</div>" + tag + "</div>"
    );
  }
  return (
    '<div class="slot" style="border-color:' + esc(border) + ";background:" + esc(bg) + '">' +
    thumb(card) +
    '<div class="body">' +
      '<div class="nm">' + esc(name) + "</div>" +
      '<div class="hp">' + (card ? hp[0] + " / " + hp[1] : "—") + "</div>" +
      bar + marks(card) +
    "</div>" + tag + "</div>"
  );
}

/** スタジアム。アプリに合わせ、自分が出したものは左、相手のものは右に置く。 */
function stadiumRow(stadium) {
  const card = stadium
    ? '<div class="stadium">' + thumb(stadium) +
      '<div><div class="stadium-label">スタジアム</div>' +
      '<div class="nm">' + esc(stadium.name) + "</div></div></div>"
    : "";
  const mine = stadium && stadium.mine;
  return (
    '<div class="divider-row">' +
    '<div class="stadium-slot left">' + (mine ? card : "") + "</div>" +
    "<i></i>" +
    '<div class="stadium-slot right">' + (stadium && !mine ? card : "") + "</div>" +
    "</div>"
  );
}

function sideHeadHtml(side, prev, which) {
  const energy = (s) => (s.active ? s.active.energy.length : 0) + s.bench.reduce((a, c) => a + c.energy.length, 0);
  const delta = (a, b) => (b === null || a === b ? "" : a - b > 0 ? " (+" + (a - b) + ")" : " (" + (a - b) + ")");
  const p = prev || null;
  const pts = [0, 1, 2]
    .map((i) => '<span class="pt ' + which + '" style="background:' + (i < side.points ? (which === "me" ? "var(--color-accent)" : "var(--color-neutral-700)") : "transparent") + '"></span>')
    .join("");
  return (
    '<div class="side-head ' + which + '">' +
      '<div style="display:flex;align-items:center;gap:8px;min-width:0">' +
        '<span class="side-badge ' + which + '">' + (which === "me" ? "自分" : "相手") + "</span>" +
        '<span class="side-name">' + esc(side.name) + "</span>" +
        '<span class="side-order">' + (which === "me" ? ME_ORDER : OPP_ORDER) + "</span>" +
      "</div>" +
      '<div class="side-stats">' +
        '<span class="pts">' + pts + "</span>" +
        "<span>ポイント <b>" + side.points + "</b>/3</span>" +
        '<span class="muted">手札 <b>' + side.hand + "</b>" + delta(side.hand, p ? p.hand : null) +
          " · 山札 <b>" + side.deck + "</b>" + delta(side.deck, p ? p.deck : null) +
          " · エネ <b>" + energy(side) + "</b>" + delta(energy(side), p ? energy(p) : null) + "</span>" +
      "</div>" +
    "</div>"
  );
}

const snaps = STEPS.map((s, i) => (s.board ? { i, s } : null)).filter(Boolean);

/** 各手番（ターン + 先攻／後攻）の先頭のインデックス。「手番」単位の移動に使う。 */
const TURN_STARTS = STEPS.map((s, i) =>
  i === 0 || STEPS[i - 1].turn !== s.turn || STEPS[i - 1].side !== s.side ? i : -1
).filter((i) => i >= 0);

function renderSummary() {
  el("summary").style.display = state.summary ? "" : "none";
  if (!state.summary || !snaps.length) return;
  const totalHp = (side) => {
    const all = [].concat(side.active ? [side.active] : [], side.bench);
    const cur = all.reduce((a, c) => a + (c.hp ? c.hp[0] : 0), 0);
    const max = all.reduce((a, c) => a + (c.hp ? c.hp[1] : 0), 0);
    return max ? cur / max : 1;
  };
  const X = (n) => (snaps.length > 1 ? (n / (snaps.length - 1)) * 620 + 10 : 320);
  const line = (key) => snaps.map((sn, n) => X(n) + "," + (120 - totalHp(sn.s.board[key]) * 118).toFixed(1)).join(" ");
  let marksHtml = "";
  snaps.forEach((sn, n) => {
    if (n === 0) return;
    const p = snaps[n - 1].s.board, c = sn.s.board;
    const tri = (k) => '<polygon points="' + (X(n) - 5) + ",128 " + (X(n) + 5) + ",128 " + X(n) + ',118" fill="' + k + '"></polygon>';
    if (c.me.points > p.me.points) marksHtml += tri("var(--color-accent)");
    if (c.opp.points > p.opp.points) marksHtml += tri("var(--color-neutral-700)");
  });
  let ticks = "";
  snaps.forEach((sn, n) => {
    const x = X(n);
    ticks +=
      '<g><line x1="' + x + '" y1="120" x2="' + x + '" y2="126" stroke="var(--color-neutral-400)"></line>' +
      (n % 2 === 0 ? '<text x="' + x + '" y="140" text-anchor="middle" font-size="11" fill="var(--color-neutral-600)">T' + sn.s.turn + "</text>" : "") +
      '<rect x="' + (x - 9) + '" y="0" width="18" height="126" fill="transparent" style="cursor:pointer" data-jump="' + sn.i + '"></rect></g>';
  });
  let cursorN = 0;
  snaps.forEach((sn, n) => { if (sn.i <= state.idx) cursorN = n; });
  el("graph").innerHTML =
    '<rect x="0" y="0" width="640" height="120" fill="none" stroke="var(--color-neutral-300)"></rect>' +
    '<line x1="0" y1="60" x2="640" y2="60" stroke="var(--color-neutral-200)"></line>' +
    '<polyline fill="none" stroke="var(--color-accent-500)" stroke-width="2.5" points="' + line("me") + '"></polyline>' +
    '<polyline fill="none" stroke="var(--color-neutral-500)" stroke-width="2.5" stroke-dasharray="5 4" points="' + line("opp") + '"></polyline>' +
    marksHtml + ticks +
    '<line x1="' + X(cursorN) + '" y1="0" x2="' + X(cursorN) + '" y2="126" stroke="var(--color-accent-700)" stroke-width="1.5"></line>';
}

function buildLists() {
  const events = [];
  let ps = null;
  STEPS.forEach((s, i) => {
    const who = isMine(s.side) ? "自分" : "相手";
    if (/に進化/.test(s.text)) events.push({ i, turn: s.turn, kind: "進化", color: "var(--color-accent-700)", text: who + " " + s.text.replace(/^.*を\s*/, "→ ") });
    else if (/ワザ/.test(s.text)) events.push({ i, turn: s.turn, kind: "ワザ", color: "var(--color-danger)", text: who + " " + s.text });
    if (s.board) {
      if (ps) {
        if (s.board.me.points > ps.me.points) events.push({ i, turn: s.turn, kind: "ポイント", color: "var(--color-accent)", text: "自分 +" + (s.board.me.points - ps.me.points) + " → " + s.board.me.points + "/3" });
        if (s.board.opp.points > ps.opp.points) events.push({ i, turn: s.turn, kind: "ポイント", color: "var(--color-neutral-800)", text: "相手 +" + (s.board.opp.points - ps.opp.points) + " → " + s.board.opp.points + "/3" });
      }
      ps = s.board;
    }
  });
  const trainers = [];
  STEPS.forEach((s, i) => {
    const m = s.text.match(/トレーナーズ「(.+?)」/);
    const ev = s.text.match(/を\s*(\S+)\s*に進化/);
    const who = isMine(s.side) ? "自" : "相";
    const color = isMine(s.side) ? "var(--color-accent-700)" : "var(--color-neutral-700)";
    if (m) trainers.push({ i, turn: s.turn, who, color, name: m[1] });
    else if (ev) trainers.push({ i, turn: s.turn, who, color, name: "進化 → " + ev[1] });
  });
  el("events").innerHTML = events
    .map((e) => '<div class="ev-row" data-jump="' + e.i + '"><span class="num">T' + e.turn + '</span><span class="chip" style="border-color:' + e.color + ";color:" + e.color + '">' + esc(e.kind) + '</span><span style="min-width:0">' + esc(e.text) + "</span></div>")
    .join("");
  el("trainers").innerHTML = trainers
    .map((t) => '<div class="tr-row" data-jump="' + t.i + '"><span class="num">T' + t.turn + '</span><span style="font-size:10px;color:' + t.color + '">' + t.who + '</span><span style="min-width:0">' + esc(t.name) + "</span></div>")
    .join("");
}

function buildLog() {
  const groups = [];
  STEPS.forEach((s, i) => {
    const g = groups[groups.length - 1];
    if (!g || g.turn !== s.turn || g.side !== s.side) {
      groups.push({
        turn: s.turn, side: s.side, who: isMine(s.side) ? "自分" : "相手",
        color: isMine(s.side) ? "var(--color-accent)" : "var(--color-neutral-700)", items: [],
      });
    }
    groups[groups.length - 1].items.push(s);
  });
  el("log").innerHTML = groups
    .map((g) =>
      '<div class="log-group"><div class="log-group-head"><span class="dot" style="background:' + g.color + '"></span><span>ターン ' + g.turn + " · " + g.who + "</span></div>" +
      g.items.map((s) => {
        const i = s.no - 1;
        return (
          '<div class="log-row" data-idx="' + i + '" data-jump="' + i + '">' +
            '<span class="log-no">' + s.no + ".</span>" +
            '<span style="min-width:0"><span class="log-text">' + esc(s.text) + "</span>" +
            (s.handList ? '<span class="log-hand">手札: ' + esc(s.handList.join("、")) + "</span>" : "") +
            "</span>" +
          "</div>"
        );
      }).join("") +
      "</div>"
    )
    .join("");
}

/** 手番の帯。再生中でも、どちらの番か・いつ切り替わったかが分かるようにする。 */
function renderTurnBanner(cur) {
  const mine = isMine(cur.side);
  el("turnWho").textContent = mine ? "自分の番" : "相手の番";
  el("turnNo").textContent = "ターン " + cur.turn;
  // 手番が変わったときだけアニメーションを付ける（アプリのカットインの代わり）
  const key = cur.turn + "/" + cur.side;
  const changed = key !== state.turnKey;
  state.turnKey = key;
  el("turnBanner").className = "turn-banner " + (mine ? "me" : "opp") + (changed ? " cut-in" : "");
}

function renderBoard() {
  const cur = STEPS[state.idx];
  let bi = -1;
  for (let i = state.idx; i >= 0; i--) if (STEPS[i].board) { bi = i; break; }
  let pi = -1;
  for (let i = bi - 1; i >= 0; i--) if (STEPS[i].board) { pi = i; break; }
  const board = bi >= 0 ? STEPS[bi].board : null;
  const prev = pi >= 0 ? STEPS[pi].board : null;
  el("boardTitle").textContent = bi >= 0 ? "#" + STEPS[bi].no + " " + STEPS[bi].text : "初期盤面";
  renderTurnBanner(cur);
  el("boardMeta").textContent = "#" + cur.no + " の直後";
  el("curNoLabel").textContent = "#" + cur.no + " を選択中";

  if (!board) { el("boardBody").innerHTML = '<p class="muted">盤面はまだありません。</p>'; return; }
  const meD = diffSide(board.me, prev && prev.me);
  const oppD = diffSide(board.opp, prev && prev.opp);
  const pad = (arr) => { const a = arr.slice(0, 3); while (a.length < 3) a.push(null); return a; };
  const bench = (list, dif) => pad(list).map((c, i) => slotHtml(c, dif["B" + i], false)).join("");

  el("boardBody").innerHTML =
    sideHeadHtml(board.opp, prev && prev.opp, "opp") +
    '<div class="zone"><div class="zone-label">相手 ベンチ</div><div class="bench">' + bench(board.opp.bench, oppD) + "</div></div>" +
    '<div class="active-wrap">' + slotHtml(board.opp.active, oppD.A, true, "相手") + "</div>" +
    stadiumRow(board.stadium) +
    '<div class="active-wrap">' + slotHtml(board.me.active, meD.A, true, "自分") + "</div>" +
    '<div class="zone"><div class="zone-label">自分 ベンチ</div><div class="bench">' + bench(board.me.bench, meD) + "</div></div>" +
    sideHeadHtml(board.me, prev && prev.me, "me") +
    (state.hand && board.hand.length
      ? '<div class="hand-zone"><div class="zone-label">' + esc(board.handLabel) + '</div><div class="hand-row">' +
        board.hand.map((h) => '<div class="hand-card">' + thumb(h) + '<div class="nm">' + esc(h.name) + "</div></div>").join("") +
        "</div></div>"
      : "");
}

function syncScroll() {
  const body = el("log");
  const row = body.querySelector('[data-idx="' + state.idx + '"]');
  if (!row) return;
  const rel = row.getBoundingClientRect().top - body.getBoundingClientRect().top;
  const h = body.getBoundingClientRect().height;
  if (rel < 24 || rel > h - 56) body.scrollTop = body.scrollTop + rel - h * 0.4;
}

function render() {
  const cur = STEPS[state.idx];
  document.querySelectorAll(".log-row.on").forEach((r) => r.classList.remove("on"));
  const row = el("log").querySelector('[data-idx="' + state.idx + '"]');
  if (row) row.classList.add("on");
  el("slider").value = state.idx;
  el("pos").textContent = "#" + cur.no + " / " + STEPS.length + " · ターン " + cur.turn + " · " + (isMine(cur.side) ? "自分" : "相手");
  el("playBtn").textContent = state.playing ? "■ 停止" : "▶ 再生";
  el("unitAction").classList.toggle("on", state.unit === "action");
  el("unitTurn").classList.toggle("on", state.unit === "turn");
  el("summaryBtn").textContent = state.summary ? "サマリーを隠す" : "サマリーを表示";
  el("handBtn").textContent = state.hand ? "手札を隠す" : "手札を表示";
  renderSummary();
  renderBoard();
  syncScroll();
}

function jump(i) { state.idx = Math.max(0, Math.min(STEPS.length - 1, i)); render(); }
function move(d) {
  if (state.unit === "turn") {
    // 手番の先頭どうしを行き来する
    let n = 0;
    TURN_STARTS.forEach((start, k) => { if (start <= state.idx) n = k; });
    jump(TURN_STARTS[Math.max(0, Math.min(TURN_STARTS.length - 1, n + d))]);
  } else jump(state.idx + d);
}
function togglePlay() {
  clearInterval(state.timer);
  if (state.playing) { state.playing = false; render(); return; }
  state.playing = true;
  state.timer = setInterval(() => {
    if (state.idx >= STEPS.length - 1) { clearInterval(state.timer); state.playing = false; render(); }
    else move(1);
  }, 700);
  render();
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-jump]");
  if (t) jump(+t.getAttribute("data-jump"));
});
window.addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); move(1); }
  else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); move(-1); }
  else if (e.key === "Home") jump(0);
  else if (e.key === "End") jump(STEPS.length - 1);
  else if (e.key === " ") { e.preventDefault(); togglePlay(); }
});
el("prev").onclick = () => move(-1);
el("next").onclick = () => move(1);
el("playBtn").onclick = togglePlay;
el("slider").oninput = (e) => jump(+e.target.value);
el("unitAction").onclick = () => { state.unit = "action"; render(); };
el("unitTurn").onclick = () => { state.unit = "turn"; render(); };
el("summaryBtn").onclick = () => { state.summary = !state.summary; render(); };
el("handBtn").onclick = () => { state.hand = !state.hand; render(); };

buildLists();
buildLog();
el("slider").max = STEPS.length - 1;
let first = 0;
for (let i = 0; i < STEPS.length; i++) if (STEPS[i].board) { first = i; break; }
jump(first);
