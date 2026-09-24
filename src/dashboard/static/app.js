"use strict";

/* Props trading desk: front end. Talks to server.py's /api endpoints. */

const POLL_MS = 1500;
const SPORT_LABEL = { nfl: "NFL", ncaaf: "NCAAF", cfl: "CFL", nba: "NBA", ncaab: "NCAAB", wnba: "WNBA",
  mlb: "MLB", nhl: "NHL", afl: "AFL", nrl: "NRL", epl: "EPL", laliga: "La Liga", bundesliga: "Bundesliga",
  seriea: "Serie A", ligue1: "Ligue 1", mls: "MLS" };
const SOCCER = new Set(["epl", "laliga", "bundesliga", "seriea", "ligue1", "mls"]);
const BOOK_NAME = { draftkings: "DraftKings", fanduel: "FanDuel", betmgm: "BetMGM", williamhill_us: "Caesars",
  espnbet: "ESPN BET", fanatics: "Fanatics", betrivers: "BetRivers", bovada: "Bovada", betonlineag: "BetOnline",
  hardrockbet: "Hard Rock Bet", ballybet: "Bally Bet", betparx: "betPARX", pointsbetus: "PointsBet",
  mybookieag: "MyBookie", lowvig: "LowVig", fliff: "Fliff", prizepicks: "PrizePicks", underdog: "Underdog" };

const S = {
  meta: null, settings: null, games: [], props: {}, byEvent: {},
  open: null, tab: {}, sport: "all", view: "all", q: "",
  live: { exposure: {}, alerts: [], bets: [], log: [], totals: {}, status: {} },
  alertMap: {}, rail: "alerts", seenBet: 0,
  marketVigs: {},   // "event_id|market_key" -> vig, for markets with their own vig
  moved: {},        // prop id -> {old: [prices], until: ms}, to show price moves
  vigTimers: {},    // pending vig changes, sent after the trader stops clicking
  queue: {},        // per-prop request queue, so fast clicks apply in order
};
const PRICE_STEP = 5;      // cents per click (Shift-click: 1)
const VIG_STEP = 0.0025;   // 0.25% per click (Shift-click: 1%)
const VIG_SEND_MS = 450;
const MOVED_MS = 6000;

/* ------------------------------------------------------------------ helpers */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtPrice = (a) => (a > 0 ? `+${a}` : `${a}`);
const fmtLine = (l) => (l == null ? "" : String(+l));
const money = (v, signed = false) => {
  const s = Math.abs(Math.round(v)).toLocaleString("en-US");
  if (!signed || Math.round(v) === 0) return `$${s}`;
  return v < 0 ? `−$${s}` : `+$${s}`;
};
const fairAmerican = (p) => {
  p = Math.min(Math.max(p, 0.01), 0.99);
  const a = Math.round(p >= 0.5 ? (-100 * p) / (1 - p) : (100 * (1 - p)) / p);
  return a > -100 && a < 100 ? 100 : a;
};
const toCents = (a) => (a >= 100 ? a - 100 : a + 100);
const fromCents = (c) => (c >= 0 ? c + 100 : c - 100);
const gameById = (id) => S.games.find((g) => g.event_id === id);
const pct = (v) => (v * 100).toFixed(2);
const bookName = (k) => BOOK_NAME[k] || k;
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const kickoff = (iso) => new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short",
  hour: "2-digit", minute: "2-digit" });

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}

let toastTimer;
function toast(msg, kind = "error") {
  let el = $(".toast");
  if (!el) { el = document.createElement("div"); el.setAttribute("role", "status"); document.body.append(el); }
  el.className = `toast ${kind}`;
  el.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 3500);
}

/* ------------------------------------------------------------------ data */

async function loadBoard() {
  let b;
  try { b = await api("/api/board"); } catch (e) { return showEmpty(e.message); }
  S.meta = b.meta; S.settings = b.settings; S.games = b.games; S.props = {}; S.byEvent = {};
  S.marketVigs = b.market_vigs || {};
  for (const p of b.props) {
    S.props[p.id] = p;
    ((S.byEvent[p.event_id] ??= {})[p.market_key] ??= []).push(p.id);
  }
  for (const markets of Object.values(S.byEvent)) {
    for (const ids of Object.values(markets)) ids.sort((a, b) => S.props[a].player.localeCompare(S.props[b].player));
  }
  renderTop(); renderSportFilter(); renderGames();
}

function showEmpty(message) {
  $("#games").innerHTML = `
    <div class="empty-state">
      <h2>No odds loaded</h2>
      <p class="muted">${esc(message)}</p>
      <p>For real odds: <code>python src/odds-api/pull_snapshot.py</code><br>
      For sample odds: <code>python src/odds-api/make_sample_data.py</code></p>
      <p class="muted">Then restart the dashboard.</p>
    </div>`;
}

function applyLive(l) {
  S.live = l;
  S.settings = l.settings;
  S.alertMap = Object.fromEntries(l.alerts.map((a) => [a.prop_id, a]));
  for (const [id, st] of Object.entries(l.status)) if (S.props[id]) S.props[id].status = st;
  renderTop();
  updateGameHeads();
  if (S.open) { updateOpenRows(); updateTabs(); }
  renderRail();
}

async function poll() {
  try { applyLive(await api("/api/live")); } catch (e) { /* server restarting; try again */ }
  setTimeout(poll, POLL_MS);
}

/* ------------------------------------------------------------------ filters */

function propVisible(p) {
  if (S.q && !p.player.toLowerCase().includes(S.q)) return false;
  if (S.view === "live" && p.status !== "live") return false;
  if (S.view === "alerts" && !S.alertMap[p.id]) return false;
  return true;
}
function visibleMarkets(g) {
  const m = S.byEvent[g.event_id] || {};
  return g.markets.filter((mk) => (m[mk.key] || []).some((id) => propVisible(S.props[id])));
}
function gameVisible(g) {
  if (S.sport !== "all" && g.sport !== S.sport) return false;
  if (!S.q && S.view === "all") return true;
  return visibleMarkets(g).length > 0;
}

/* ------------------------------------------------------------------ top bar */

function renderTop() {
  if (!S.meta) return;
  const m = S.meta;
  const when = m.pulled_at ? new Date(m.pulled_at).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "unknown";
  $("#snapshot").innerHTML = `Odds snapshot ${esc(when)}, ${m.bookmakers.length} books` +
    (m.source === "sample" ? ` <span class="badge-sample" title="Invented players and prices from make_sample_data.py">Sample data</span>` : "");

  const t = S.live.totals || {};
  $("#kpis").innerHTML = [
    ["Live props", (t.live ?? 0).toLocaleString()],
    ["Bets", (t.bets ?? 0).toLocaleString()],
    ["Handle", money(t.handle ?? 0)],
    ["Worst case", `<span class="${(t.worst ?? 0) < 0 ? "loss" : ""}">${money(t.worst ?? 0, true)}</span>`],
  ].map(([k, v]) => `<div class="kpi"><span class="v">${v}</span><span class="k">${k}</span></div>`).join("");

  const dv = $("#default-vig");
  if (!$(".pending", dv)) dv.innerHTML = vigStepperHTML("default", S.settings.default_vig, false, "default vig");
  const sim = $("#sim");
  sim.setAttribute("aria-pressed", String(!!S.settings.simulator));
  sim.textContent = S.settings.simulator ? "Simulated bets on" : "Simulated bets paused";
}

function renderSportFilter() {
  const sports = [...new Set(S.games.map((g) => g.sport))];
  $("#sport-filter").innerHTML = ["all", ...sports].map((s) =>
    `<button data-sport="${s}" aria-pressed="${S.sport === s}">${s === "all" ? "All sports" : esc(SPORT_LABEL[s] || s)}</button>`).join("");
}

/* ------------------------------------------------------------------ games */

function gameStats(eid) {
  let live = 0, total = 0, handle = 0, alerts = 0;
  for (const ids of Object.values(S.byEvent[eid] || {})) {
    for (const id of ids) {
      total++;
      if (S.props[id].status === "live") live++;
      const ex = S.live.exposure[id];
      if (ex) handle += ex.total;
      if (S.alertMap[id]) alerts++;
    }
  }
  return { live, total, handle, alerts };
}

function gameLinesText(g) {
  const l = g.lines || {};
  const short = SOCCER.has(g.sport) ? g.home_team : g.home_team.split(" ").slice(-1)[0];
  const parts = [];
  if (l.home_spread != null) parts.push(`${esc(short)} ${l.home_spread > 0 ? "+" : ""}${l.home_spread}`);
  if (l.total != null) parts.push(`Total ${l.total}`);
  const mixed = Object.keys(S.marketVigs).some((k) => k.startsWith(g.event_id + "|")) ||
    Object.values(S.byEvent[g.event_id] || {}).some((ids) => ids.some((id) => S.props[id].vig_source === "prop"));
  const vigTitle = mixed ? "Some markets or props in this game have their own vig"
    : g.vig_custom ? "Vig set for this game" : "Default vig";
  const vig = `<span class="${g.vig_custom || mixed ? "vig-custom" : ""}" title="${vigTitle}">Vig ${pct(g.vig)}%${mixed ? ", mixed" : ""}</span>`;
  return parts.map((p) => `<span>${p}</span>`).join("") + vig;
}

function gameHTML(g) {
  const open = S.open === g.event_id;
  const st = gameStats(g.event_id);
  return `
  <section class="game ${open ? "open" : ""}" data-event="${g.event_id}">
    <button class="game-head" data-act="toggle" aria-expanded="${open}">
      <span class="chev" aria-hidden="true">▸</span>
      <span class="sport-tag">${esc(SPORT_LABEL[g.sport] || g.sport)}</span>
      <span class="teams">${esc(g.away_team)}<span class="at">at</span>${esc(g.home_team)}</span>
      <span class="when">${esc(kickoff(g.commence_time))}</span>
      <span class="glines">${gameLinesText(g)}</span>
      <span class="stat live-count"><b>${st.live}</b> of ${st.total} live</span>
      <span class="stat handle">${st.handle ? money(st.handle) : ""}</span>
      <span class="alert-count" title="Props that need attention">${st.alerts || ""}</span>
    </button>
    <div class="game-body" ${open ? "" : "hidden"}></div>
  </section>`;
}

function renderGames() {
  const el = $("#games");
  const list = S.games.filter(gameVisible);
  if (!S.games.length) { el.innerHTML = `<div class="empty-state"><h2>No games in this snapshot</h2></div>`; return; }
  if (!list.length) {
    el.innerHTML = `<div class="empty-state"><h2>Nothing matches these filters</h2>
      <p class="muted">Try All props, All sports, or clear the player search.</p></div>`;
    return;
  }
  el.innerHTML = list.map(gameHTML).join("");
  if (S.open && list.some((g) => g.event_id === S.open)) renderBody(S.open);
}

function updateGameHeads() {
  for (const sec of $$(".game")) {
    const st = gameStats(sec.dataset.event);
    $(".live-count", sec).innerHTML = `<b>${st.live}</b> of ${st.total} live`;
    $(".handle", sec).textContent = st.handle ? money(st.handle) : "";
    $(".alert-count", sec).textContent = st.alerts || "";
  }
}

/* ------------------------------------------------------------------ game body */

function renderBody(eid) {
  const sec = $(`.game[data-event="${eid}"]`);
  if (!sec) return;
  const body = $(".game-body", sec);
  const g = gameById(eid);
  const mkts = visibleMarkets(g);
  if (!mkts.length) {
    body.innerHTML = `<div class="toolbar muted">${g.markets.length
      ? "No props match the current filters."
      : "No player props posted for this game yet. Books usually post them closer to the start."}</div>`;
    return;
  }
  let tab = S.tab[eid];
  if (!mkts.some((m) => m.key === tab)) tab = S.tab[eid] = mkts[0].key;
  const mname = mkts.find((m) => m.key === tab).name;
  const ids = (S.byEvent[eid][tab] || []).filter((id) => propVisible(S.props[id]));
  const sides = S.props[ids[0]].sides;
  const mKey = `${eid}|${tab}`;
  const mCustom = S.marketVigs[mKey] != null;
  const mVig = mCustom ? S.marketVigs[mKey] : g.vig;

  body.innerHTML = `
    <div class="tabs" role="tablist" aria-label="Markets">${mkts.map((m) => tabHTML(eid, m, tab)).join("")}</div>
    <div class="toolbar vig-bar">
      <span class="ctl" title="Sets every market and prop in this game">Game vig
        ${vigStepperHTML("game", g.vig, g.vig_custom, "game vig")}</span>
      ${g.vig_custom ? `<button class="btn small quiet" data-act="vig-clear" data-scope="game">Use default (${pct(S.settings.default_vig)}%)</button>` : ""}
      <span class="ctl" title="Sets every ${esc(mname)} prop in this game">${esc(mname)} vig
        ${vigStepperHTML("market", mVig, mCustom, `${mname} vig`, tab)}</span>
      ${mCustom ? `<button class="btn small quiet" data-act="vig-clear" data-scope="market" data-market="${tab}">Use game vig (${pct(g.vig)}%)</button>` : ""}
      <span class="spacer"></span>
      <span class="muted hint">Grey vig is inherited, amber is set at that level. ↺ resets a prop.</span>
    </div>
    <div class="toolbar">
      <button class="btn small" data-act="bulk" data-scope="market" data-status="live">Offer all ${esc(mname)}</button>
      <button class="btn small quiet" data-act="bulk" data-scope="market" data-status="off">Pull all ${esc(mname)}</button>
      <span class="spacer"></span>
      <button class="btn small quiet" data-act="bulk" data-scope="game" data-status="live">Offer whole game</button>
      <button class="btn small quiet" data-act="bulk" data-scope="game" data-status="suspended">Suspend live props</button>
      <button class="btn small quiet" data-act="bulk" data-scope="game" data-status="off">Pull whole game</button>
    </div>
    <div class="table-wrap">
      <table class="props">
        <colgroup>
          <col class="c-player"><col class="c-line"><col class="c-price"><col class="c-price"><col class="c-vig">
          <col class="c-fair"><col class="c-money"><col class="c-pnl"><col class="c-status"><col class="c-reset">
        </colgroup>
        <thead><tr>
          <th>Player</th>
          <th>Line</th>
          <th title="Arrows move this side 5 cents (Shift: 1 cent). The other side adjusts to keep the vig."><span class="side-a">${esc(sides[0])}</span></th>
          <th title="Arrows move this side 5 cents (Shift: 1 cent). The other side adjusts to keep the vig.">${sides[1] ? `<span class="side-b">${esc(sides[1])}</span>` : ""}</th>
          <th title="Vig on this prop. Arrows set this prop's own vig (amber). Grey means inherited from the market.">Vig</th>
          <th title="Books' consensus price with their vig removed. Click the books link to see each book.">Fair</th>
          <th title="How much has been bet on each side of this prop">Money bet</th>
          <th class="r" title="What the sportsbook wins (+) or loses (−) depending on the result">Book P/L if…</th>
          <th>Status</th><th></th>
        </tr></thead>
        <tbody>${ids.map((id) => rowHTML(S.props[id])).join("")}</tbody>
      </table>
    </div>`;
}

function tabHTML(eid, m, active) {
  const ids = S.byEvent[eid][m.key] || [];
  const live = ids.filter((id) => S.props[id].status === "live").length;
  const alerting = ids.some((id) => S.alertMap[id]);
  return `<button class="tab ${alerting ? "alerting" : ""}" role="tab" data-act="tab" data-market="${m.key}"
    aria-selected="${m.key === active}">${esc(m.name)}<span class="n ${live ? "has-live" : ""}">${live}/${ids.length}</span></button>`;
}

function updateTabs() {
  const sec = $(`.game[data-event="${S.open}"]`);
  if (!sec) return;
  for (const t of $$(".tab", sec)) {
    const ids = S.byEvent[S.open][t.dataset.market] || [];
    const live = ids.filter((id) => S.props[id].status === "live").length;
    t.classList.toggle("alerting", ids.some((id) => S.alertMap[id]));
    const n = $(".n", t);
    n.textContent = `${live}/${ids.length}`;
    n.classList.toggle("has-live", live > 0);
  }
}

const VIG_SOURCE = { prop: "prop's own vig", market: "market vig", game: "game vig", default: "default vig" };

function priceHTML(p, i) {
  const side = esc(p.sides[i]);
  const m = S.moved[p.id];
  const was = m && m.old[i] !== p.prices[i] ? m.old[i] : null;
  return `<div class="stepper price ${i ? "b" : "a"} ${was != null ? "moved" : ""}" role="group" aria-label="${side} price">
      <button class="arr" data-act="move" data-side="${i}" data-d="-1" title="Shorten ${side} ${PRICE_STEP}¢ (Shift: 1¢)" aria-label="Shorten ${side}">▾</button>
      <span class="val">${fmtPrice(p.prices[i])}</span>
      <button class="arr" data-act="move" data-side="${i}" data-d="1" title="Lengthen ${side} ${PRICE_STEP}¢ (Shift: 1¢)" aria-label="Lengthen ${side}">▴</button>
    </div>${was != null ? `<s class="was" title="Price before the last change">${fmtPrice(was)}</s>` : ""}`;
}

function vigStepperHTML(scope, value, own, label, market = "") {
  return `<span class="stepper vig ${own ? "own" : ""}" data-scope="${scope}" data-value="${value}" data-market="${market}"
      role="group" aria-label="${esc(label)}">
      <button class="arr" data-act="vig-step" data-d="-1" title="−0.25% (Shift: −1%)" aria-label="Lower ${esc(label)}">▾</button>
      <span class="val">${pct(value)}%</span>
      <button class="arr" data-act="vig-step" data-d="1" title="+0.25% (Shift: +1%)" aria-label="Raise ${esc(label)}">▴</button>
    </span>`;
}

function moneyHTML(p, ex, al) {
  const fix = al ? `<button class="fix" data-act="shade" data-side="${al.side_idx}" data-cents="${al.suggest_cents}"
    title="${esc(al.reason)}">Shade ${esc(al.side)} ${al.suggest_cents}¢</button>` : "";
  if (!ex) return `<div class="money-empty">${p.status === "live" ? "No bets yet" : ""}</div>${fix}`;
  const bets = `${ex.bets} bet${ex.bets === 1 ? "" : "s"}`;
  if (p.two_way) {
    const a = ex.total ? (ex.handle[0] / ex.total) * 100 : 50;
    return `<div class="split" role="img" aria-label="${Math.round(a)}% of money on ${esc(p.sides[0])}">
        <span class="a" style="width:${a}%"></span><span class="b" style="width:${100 - a}%"></span></div>
      <div class="money-meta"><span class="side-a">${money(ex.handle[0])}</span><span>${bets}</span>
        <span class="side-b">${money(ex.handle[1])}</span></div>${fix}`;
  }
  const fill = Math.min(100, (ex.total / 2000) * 100);
  return `<div class="split oneway" role="img" aria-label="${money(ex.total)} on ${esc(p.sides[0])}">
      <span class="a" style="width:${fill}%"></span></div>
    <div class="money-meta"><span class="side-a">${money(ex.total)} on ${esc(p.sides[0])}</span><span>${bets}</span></div>${fix}`;
}

function pnlHTML(p, ex) {
  if (!ex) return "";
  const rows = p.two_way
    ? [[p.sides[0], ex.pnl_if[0]], [p.sides[1], ex.pnl_if[1]]]
    : [[p.sides[0], ex.pnl_if[0]], ["not", ex.total]];
  return rows.map(([who, v]) => `<div class="row"><span class="who">${esc(who)}</span>
    <span class="${v < 0 ? "loss" : "gain"}">${money(v, true)}</span></div>`).join("");
}

function statusHTML(p) {
  const labels = { off: "Off", live: "Live", suspended: "Susp." };
  const titles = { off: "Not offered", live: "Offered on the sportsbook", suspended: "Shown but not taking bets" };
  return `<div class="status" role="group" aria-label="Status">${["off", "live", "suspended"].map((s) =>
    `<button data-act="status" data-status="${s}" class="${p.status === s ? "on" : ""}" aria-pressed="${p.status === s}" title="${titles[s]}">${labels[s]}</button>`).join("")}</div>`;
}

function rowHTML(p) {
  const ex = S.live.exposure[p.id];
  const al = S.alertMap[p.id];
  const ownVig = p.vig_source === "prop";
  return `<tr data-id="${esc(p.id)}" class="st-${p.status} ${al ? "alerting" : ""}">
    <td class="who"><span class="player" title="${esc(p.player)}">${esc(p.player)}</span>${
      p.manual ? `<span class="edited" title="Prices moved by a trader">edited</span>` : ""}</td>
    <td class="line">${p.line == null ? "" : fmtLine(p.line)}</td>
    <td>${priceHTML(p, 0)}</td>
    <td>${p.two_way ? priceHTML(p, 1) : ""}</td>
    <td title="${ownVig ? "Set on this prop" : `From the ${VIG_SOURCE[p.vig_source]}`}">${vigStepperHTML("prop", p.vig_target, ownVig, `vig for ${p.player}`)}</td>
    <td class="fair"><span class="muted">${p.fair.map((f) => fmtPrice(fairAmerican(f))).join(" / ")}</span>
      <button class="books-btn" data-act="quotes">${p.books} book${p.books === 1 ? "" : "s"}</button></td>
    <td class="money">${moneyHTML(p, ex, al)}</td>
    <td class="r pnl">${pnlHTML(p, ex)}</td>
    <td class="status-cell">${statusHTML(p)}</td>
    <td><button class="icon-btn" data-act="reset" title="Reset prices and vig to consensus" aria-label="Reset to consensus"
      ${p.manual || ownVig ? "" : "disabled"}>↺</button></td>
  </tr>`;
}

function findRow(id) {
  return $(`tr[data-id="${CSS.escape(id)}"]`);
}

function rerenderRow(id) {
  const tr = findRow(id);
  if (tr) tr.outerHTML = rowHTML(S.props[id]);
}

function updateOpenRows() {
  const sec = $(`.game[data-event="${S.open}"]`);
  if (!sec) return;
  for (const tr of $$("tbody tr", sec)) {
    const p = S.props[tr.dataset.id];
    if (!p) continue;
    const ex = S.live.exposure[p.id];
    const al = S.alertMap[p.id];
    tr.classList.toggle("alerting", !!al);
    if (!tr.classList.contains(`st-${p.status}`)) {
      tr.className = `st-${p.status} ${al ? "alerting" : ""}`;
      $(".status-cell", tr).innerHTML = statusHTML(p);
    }
    $(".money", tr).innerHTML = moneyHTML(p, ex, al);
    $(".pnl", tr).innerHTML = pnlHTML(p, ex);
  }
}

/* ------------------------------------------------------------------ actions */

/* Vig: default -> game -> market -> prop. The most specific level wins. */

async function setVig(scope, vig, where = {}) {
  try {
    const res = await api("/api/vig", { scope, vig, ...where });
    markMoved(res.moved);
    await loadBoard();
    toast(res.repriced ? `Repriced ${res.repriced} prop${res.repriced === 1 ? "" : "s"}` : "No prices changed", "info");
  } catch (e) {
    toast(e.message);
    if (S.open) renderBody(S.open);
    renderTop();
  }
}

function vigWhere(stepper) {
  const scope = stepper.dataset.scope;
  const eid = stepper.closest(".game")?.dataset.event;
  if (scope === "prop") return { id: stepper.closest("tr").dataset.id };
  if (scope === "market") return { event_id: eid, market_key: stepper.dataset.market };
  if (scope === "game") return { event_id: eid };
  return {};
}

/* Vig arrows update the number straight away; the change is sent once the trader stops clicking. */
function vigStep(btn, big) {
  const st = btn.closest(".stepper");
  const step = (big ? 0.01 : VIG_STEP) * Number(btn.dataset.d);
  const v = Math.min(0.25, Math.max(0, Math.round((Number(st.dataset.value) + step) * 10000) / 10000));
  st.dataset.value = v;
  $(".val", st).textContent = `${pct(v)}%`;
  st.classList.add("pending", "own");
  const where = vigWhere(st);
  const key = `${st.dataset.scope}|${JSON.stringify(where)}`;
  clearTimeout(S.vigTimers[key]);
  S.vigTimers[key] = setTimeout(() => { delete S.vigTimers[key]; setVig(st.dataset.scope, v, where); }, VIG_SEND_MS);
}

/* Price moves: flash changed prices and show the old price for a few seconds */

let movedTimer;
function markMoved(moved) {
  const until = Date.now() + MOVED_MS;
  for (const [id, m] of Object.entries(moved || {})) S.moved[id] = { old: m.old, until };
  clearTimeout(movedTimer);
  movedTimer = setTimeout(clearMoved, MOVED_MS + 100);
}

function clearMoved() {
  const now = Date.now();
  for (const [id, m] of Object.entries(S.moved)) {
    if (m.until > now) continue;
    delete S.moved[id];
    const tr = findRow(id);
    if (tr && !tr.contains(document.activeElement)) rerenderRow(id);
  }
  if (Object.keys(S.moved).length) movedTimer = setTimeout(clearMoved, 500);
}

function propAction(id, action, extra = {}) {
  // Queue per prop so fast clicks are applied in the order they were made
  S.queue[id] = (S.queue[id] || Promise.resolve()).then(() => doPropAction(id, action, extra));
  return S.queue[id];
}

async function doPropAction(id, action, extra) {
  const before = [...S.props[id].prices];
  try {
    const p = await api("/api/prop", { id, action, ...extra });
    delete p.book_quotes;
    Object.assign(S.props[id], p);
    if (!["live", "off", "suspended"].includes(action)) delete S.alertMap[id]; // trader reacted
    if (p.prices.some((x, i) => x !== before[i])) {
      const prev = S.moved[id];
      markMoved({ [id]: { old: prev ? prev.old : before } });  // keep the original price across quick clicks
    }
  } catch (e) {
    toast(e.message);
  }
  rerenderRow(id);
  updateGameHeads();
  updateTabs();
  renderRail();
}

async function bulk(eid, scope, status) {
  const market = scope === "market" ? S.tab[eid] : null;
  if (status === "off" && scope === "game" && !confirm("Pull every prop in this game from the sportsbook?")) return;
  try {
    const res = await api("/api/bulk", { event_id: eid, market_key: market, status });
    for (const [id, eff] of Object.entries(res)) Object.assign(S.props[id], eff);
  } catch (e) { toast(e.message); }
  renderBody(eid);
  updateGameHeads();
}

async function showQuotes(id, anchor) {
  closePopover();
  let quotes;
  try { quotes = await api(`/api/quotes?id=${encodeURIComponent(id)}`); } catch (e) { return toast(e.message); }
  const p = S.props[id];
  const others = p.lines_seen.filter((l) => l !== p.consensus_line);
  const pop = document.createElement("div");
  pop.className = "popover";
  pop.setAttribute("role", "dialog");
  pop.innerHTML = `
    <h3>${esc(p.player)} ${esc(p.market_name)}</h3>
    <div class="sub">${p.consensus_line != null ? `Consensus line ${fmtLine(p.consensus_line)}` : "No line"}${
      others.length ? `. Also posted at ${others.map(fmtLine).join(", ")}` : ""}</div>
    <table>
      <tr><th>Book</th>${p.sides.map((s, i) => `<th class="${i ? "side-b" : "side-a"}">${esc(s)}</th>`).join("")}</tr>
      ${quotes.map((q) => `<tr><td>${esc(bookName(q.book))}</td>${p.sides.map((s) => `<td>${q[s] != null ? fmtPrice(q[s]) : ""}</td>`).join("")}</tr>`).join("")}
      <tr class="off-line"><td>Fair (no vig)</td>${p.fair.map((f) => `<td>${fmtPrice(fairAmerican(f))}</td>`).join("")}</tr>
      <tr><td><b>Our price</b></td>${p.prices.map((x) => `<td><b>${fmtPrice(x)}</b></td>`).join("")}</tr>
    </table>
    <div class="sub" style="margin:8px 0 0">${p.vig != null
      ? `Our vig on this prop: ${pct(p.vig)}% (set to ${pct(p.vig_target)}%, from the ${VIG_SOURCE[p.vig_source]})`
      : `Yes-only prop: priced at twice the vig (${pct(2 * p.vig_target)}%), from the ${VIG_SOURCE[p.vig_source]}`}</div>`;
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  const top = Math.min(r.bottom + 6, window.innerHeight - pop.offsetHeight - 10);
  const left = Math.min(r.left, window.innerWidth - pop.offsetWidth - 10);
  pop.style.top = `${Math.max(10, top)}px`;
  pop.style.left = `${Math.max(10, left)}px`;
}
function closePopover() { $(".popover")?.remove(); }

function goToProp(id) {
  const p = S.props[id];
  if (!p) return;
  if (!propVisible(p) || (S.sport !== "all" && S.sport !== gameById(p.event_id).sport)) {
    S.view = "all"; S.q = ""; S.sport = "all";
    $("#search").value = "";
    $$("#view-filter button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === "all")));
    renderSportFilter();
  }
  S.open = p.event_id;
  S.tab[p.event_id] = p.market_key;
  renderGames();
  const tr = findRow(id);
  if (tr) {
    tr.scrollIntoView({ block: "center", behavior: "smooth" });
    tr.classList.add("flash");
  }
}

/* ------------------------------------------------------------------ rail */

function renderRail() {
  const n = S.live.alerts.length;
  const badge = $("#n-alerts");
  badge.textContent = n ? n : "";
  badge.classList.toggle("hot", n > 0);
  const el = $("#rail");

  if (S.rail === "alerts") {
    el.innerHTML = n ? S.live.alerts.map((a) => {
      const g = gameById(a.event_id);
      return `<div class="alert-card ${a.severity}">
        <div class="t">${esc(a.player)}, ${esc(a.market_name)}</div>
        <div class="g">${esc(g.away_team)} at ${esc(g.home_team)}</div>
        <div class="r">${esc(a.reason)}</div>
        <div class="acts">
          <button class="btn small" data-act="rail-shade" data-id="${esc(a.prop_id)}" data-side="${a.side_idx}" data-cents="${a.suggest_cents}">Shade ${esc(a.side)} ${a.suggest_cents}¢</button>
          <button class="btn small quiet" data-act="rail-suspend" data-id="${esc(a.prop_id)}">Suspend</button>
          <button class="btn small quiet" data-act="rail-show" data-id="${esc(a.prop_id)}">Show</button>
        </div></div>`;
    }).join("") : `<div class="rail-empty">Nothing needs attention. Props show up here when money gets lopsided or a result would cost us more than $1,500.</div>`;
  } else if (S.rail === "bets") {
    const bets = S.live.bets;
    el.innerHTML = bets.length ? bets.map((b) => `
      <div class="feed-item ${b.id > S.seenBet ? "new" : ""} ${b.big ? "big" : ""}">
        <span class="time">${clock(b.ts)}</span>
        <span class="what"><b>${esc(b.player)}</b> <span class="${b.side_idx ? "side-b" : "side-a"}">${esc(b.side)}${b.line != null ? " " + fmtLine(b.line) : ""}</span>
          ${esc(b.market_name)} at ${fmtPrice(b.price)}</span>
        <span class="stake">${money(b.stake)}</span>
      </div>`).join("") : `<div class="rail-empty">No bets yet. Set some props to Live and bets will start arriving.</div>`;
    if (bets.length) S.seenBet = Math.max(S.seenBet, bets[0].id);
  } else {
    const log = S.live.log;
    el.innerHTML = log.length ? log.map((x) => `
      <div class="log-item"><span class="time">${clock(x.ts)}</span><span>${esc(x.text)}</span></div>`).join("")
      : `<div class="rail-empty">Trader changes will be listed here.</div>`;
  }
}

/* ------------------------------------------------------------------ events */

function wire() {
  const games = $("#games");

  games.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const sec = btn.closest(".game");
    const eid = sec?.dataset.event;
    const id = btn.closest("tr")?.dataset.id;
    const act = btn.dataset.act;

    if (act === "toggle") { S.open = S.open === eid ? null : eid; renderGames(); }
    else if (act === "tab") { S.tab[eid] = btn.dataset.market; renderBody(eid); }
    else if (act === "status") { if (S.props[id].status !== btn.dataset.status) propAction(id, btn.dataset.status); }
    else if (act === "move") propAction(id, "move", { side: Number(btn.dataset.side),
      cents: Number(btn.dataset.d) * (e.shiftKey ? 1 : PRICE_STEP) });
    else if (act === "vig-step") vigStep(btn, e.shiftKey);
    else if (act === "shade") propAction(id, "shade", { side: Number(btn.dataset.side), cents: Number(btn.dataset.cents) });
    else if (act === "reset") propAction(id, "reset");
    else if (act === "quotes") { e.stopPropagation(); showQuotes(id, btn); }
    else if (act === "bulk") bulk(eid, btn.dataset.scope, btn.dataset.status);
    else if (act === "vig-clear") setVig(btn.dataset.scope, null, btn.dataset.scope === "market"
      ? { event_id: eid, market_key: btn.dataset.market } : { event_id: eid });
  });

  document.addEventListener("click", (e) => { if (!e.target.closest(".popover")) closePopover(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopover(); });

  $("#rail").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.act === "rail-shade") propAction(id, "shade", { side: Number(btn.dataset.side), cents: Number(btn.dataset.cents) });
    else if (btn.dataset.act === "rail-suspend") propAction(id, "suspended");
    else if (btn.dataset.act === "rail-show") goToProp(id);
  });
  $$(".rail-tabs button").forEach((b) => b.addEventListener("click", () => {
    S.rail = b.dataset.rail;
    $$(".rail-tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    renderRail();
  }));

  $("#sport-filter").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    S.sport = b.dataset.sport;
    renderSportFilter(); renderGames();
  });
  $("#view-filter").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    S.view = b.dataset.view;
    $$("#view-filter button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderGames();
  });
  let searchTimer;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { S.q = e.target.value.trim().toLowerCase(); renderGames(); }, 150);
  });

  $("#default-vig").addEventListener("click", (e) => {
    const btn = e.target.closest('[data-act="vig-step"]');
    if (btn) vigStep(btn, e.shiftKey);
  });

  $("#sim").addEventListener("click", async () => {
    try { S.settings = await api("/api/settings", { simulator: !S.settings.simulator }); renderTop(); } catch (e) { toast(e.message); }
  });
  $("#clear-bets").addEventListener("click", async () => {
    if (!confirm("Clear all bets and exposure? Prop statuses and prices stay as they are.")) return;
    try { await api("/api/bets/clear", {}); S.seenBet = 0; } catch (e) { toast(e.message); }
  });
}

wire();
loadBoard().then(poll);
