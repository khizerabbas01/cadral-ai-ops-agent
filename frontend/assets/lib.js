// Core helpers shared by every page. No dependencies and no build step.
// All dynamic text is inserted as text nodes (never innerHTML), so customer messages cannot inject markup.

export const state = { user: null, token: localStorage.getItem("token"), status: null };

const SVG_NS = "http://www.w3.org/2000/svg";

function append(el, kids) {
  for (const kid of kids.flat(9)) {
    if (kid == null || kid === false || kid === "") continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/** Create an HTML element: h("div", {class: "card", onclick}, child, [children], "text"). */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "value") value = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, kids);
  if (value !== undefined) el.value = value; // after children, so <select> can find its option
  return el;
}

/** Create an SVG element. */
export function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  return append(el, kids);
}

/** Replace an element's children (null / false children are skipped). */
export function set(el, ...kids) {
  el.replaceChildren();
  return append(el, kids);
}

// ------------------------------------------------------------------ icons (24px outline set)
const P = (d) => ["path", { d }];
const C = (cx, cy, r) => ["circle", { cx, cy, r }];
const R = (x, y, width, height, rx) => ["rect", { x, y, width, height, rx }];
const ICONS = {
  home: [P("M3 10.6 12 3l9 7.6V20a1 1 0 0 1-1 1h-5.5v-6h-5v6H4a1 1 0 0 1-1-1z")],
  chat: [P("M20 15.5a2 2 0 0 1-2 2H8.5L4 21V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z")],
  layers: [P("M12 3 3 7.8l9 4.8 9-4.8z"), P("m3 12.2 9 4.8 9-4.8"), P("m3 16.5 9 4.8 9-4.8")],
  book: [P("M12 6.5C10.2 5.2 7.6 4.5 3 4.5v14c4.6 0 7.2.7 9 2 1.8-1.3 4.4-2 9-2v-14c-4.6 0-7.2.7-9 2z"), P("M12 6.5v14")],
  zap: [P("M13 2.5 4.5 13.5H11l-1 8L18.5 10.5H12z")],
  bars: [P("M5 20v-6M10 20V9M15 20v-9M20 20V4")],
  pie: [P("M20.6 15.2A9 9 0 1 1 8.8 3.4"), P("M21 12a9 9 0 0 0-9-9v9z")],
  nodes: [C(12, 5.5, 2.5), C(5.5, 18.5, 2.5), C(18.5, 18.5, 2.5), P("M12 8v3.5M12 11.5 7 16.4M12 11.5l5 4.9")],
  settings: [C(12, 12, 2.6), C(12, 12, 6.6), P("M12 2.6v2.8M12 18.6v2.8M2.6 12h2.8M18.6 12h2.8M5.35 5.35l2 2M16.65 16.65l2 2M5.35 18.65l2-2M16.65 7.35l2-2")],
  bell: [P("M6 9.5a6 6 0 0 1 12 0c0 5.5 2.2 7 2.2 7H3.8S6 15 6 9.5z"), P("M10 19.5a2.2 2.2 0 0 0 4 0")],
  calendar: [R(3.5, 5, 17, 15.5, 2.5), P("M3.5 10h17M8 3v4M16 3v4")],
  chevronDown: [P("m6 9 6 6 6-6")],
  chevronRight: [P("m9 6 6 6-6 6")],
  arrowRight: [P("M5 12h14M13 6l6 6-6 6")],
  arrowUp: [P("M12 19V5M6 11l6-6 6 6")],
  arrowDown: [P("M12 5v14M6 13l6 6 6-6")],
  check: [P("m5 12.5 4.5 4.5L19 7.5")],
  x: [P("M6 6l12 12M18 6 6 18")],
  clock: [C(12, 12, 9), P("M12 7v5l3.5 2")],
  target: [C(12, 12, 9), C(12, 12, 5), C(12, 12, 1.2)],
  alert: [P("M12 4 2.8 20h18.4z"), P("M12 10v4.5M12 17.3v.2")],
  pencil: [P("M4 20l1-4.2L16.6 4.2a2 2 0 0 1 2.9 0l.3.3a2 2 0 0 1 0 2.9L8.2 19z"), P("m14.5 6.3 3.2 3.2")],
  users: [C(9, 8, 3.4), P("M2.8 20v-1.2a4.3 4.3 0 0 1 4.3-4.3h3.8a4.3 4.3 0 0 1 4.3 4.3V20"), P("M16 4.9a3.4 3.4 0 0 1 0 6.3M21.2 20v-1.2a4.3 4.3 0 0 0-3-4.1")],
  search: [C(11, 11, 6.5), P("m16 16 4.5 4.5")],
  refresh: [P("M20 11a8 8 0 0 0-14.3-4.5L4 8.5M4 4v4.5h4.5"), P("M4 13a8 8 0 0 0 14.3 4.5l1.7-2M20 20v-4.5h-4.5")],
  send: [P("M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5z")],
  mail: [R(3, 5, 18, 14, 2.5), P("m3.5 7.5 8.5 6 8.5-6")],
  globe: [C(12, 12, 9), P("M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9S14.5 18.4 12 21c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z")],
  plane: [P("M21.5 3.5 2.5 10.8l6.3 2.4M21.5 3.5l-3.3 16-9.4-6.3M21.5 3.5 8.8 13.2v5.3l3.1-3.2")],
  hash: [P("M9 3 7 21M17 3l-2 18M4 8.5h17M3 15.5h17")],
  database: [["ellipse", { cx: 12, cy: 6, rx: 8, ry: 3 }], P("M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"), P("M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6")],
  shield: [P("M12 3 4.5 6v5.5c0 4.6 3.1 8.3 7.5 9.5 4.4-1.2 7.5-4.9 7.5-9.5V6z"), P("m9 12 2.2 2.2L15.2 10")],
  cpu: [R(6, 6, 12, 12, 2), R(9.5, 9.5, 5, 5, 0.8), P("M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3")],
  file: [P("M6 3h8l5 5v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"), P("M14 3v5h5M8.5 13h7M8.5 16.5h7")],
  external: [P("M14 4h6v6M20 4l-9 9M18 13.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5.5")],
  plus: [P("M12 5v14M5 12h14")],
  play: [P("M7 4.5v15l12-7.5z")],
  download: [P("M12 4v11M7 10.5l5 5 5-5M5 20h14")],
  menu: [P("M4 6.5h16M4 12h16M4 17.5h16")],
  logout: [P("M9.5 20H6a1.5 1.5 0 0 1-1.5-1.5v-13A1.5 1.5 0 0 1 6 4h3.5M15 16l4-4-4-4M19 12H9.5")],
  sparkle: [P("M12 3.5 13.8 9l5.7 1.8-5.7 1.8L12 18l-1.8-5.4L4.5 10.8 10.2 9z")],
  box: [P("M12 3 4 7v10l8 4 8-4V7z"), P("M4 7l8 4 8-4M12 11v10")],
  route: [C(6, 18, 2.5), C(18, 6, 2.5), P("M8.5 18H14a3.5 3.5 0 0 0 0-7h-4a3.5 3.5 0 0 1 0-7h5.5")],
  inbox: [P("M3.5 13 6 5.5a1.5 1.5 0 0 1 1.4-1h9.2a1.5 1.5 0 0 1 1.4 1L20.5 13v5a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18z"), P("M3.5 13H8l1.5 2.5h5L16 13h4.5")],
  wrench: [P("M14.5 6.5a4 4 0 0 0 5 5L11 20a2.1 2.1 0 0 1-3-3l8.5-8.5a4 4 0 0 1-2-2z"), P("M14.5 6.5 17 4")],
  headset: [P("M4.5 14v-2a7.5 7.5 0 0 1 15 0v2"), R(3, 13.5, 4, 6, 1.5), R(17, 13.5, 4, 6, 1.5), P("M19 19.5c0 1.2-1.5 2-4 2")],
  brain: [P("M9 4.5a3 3 0 0 0-3 3 3 3 0 0 0-1.5 5.3A3 3 0 0 0 6 17.5a3 3 0 0 0 6 .5V6a2.9 2.9 0 0 0-3-1.5z"), P("M15 4.5a3 3 0 0 1 3 3 3 3 0 0 1 1.5 5.3 3 3 0 0 1-1.5 4.7 3 3 0 0 1-6 .5")],
  info: [C(12, 12, 9), P("M12 11v5.5M12 7.6v.2")],
};

export function icon(name, size = 20, cls = "") {
  const el = s("svg", { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": 1.8,
    "stroke-linecap": "round", "stroke-linejoin": "round", class: `icon ${cls}`.trim(), "aria-hidden": "true" });
  for (const [tag, attrs] of ICONS[name] || ICONS.info) el.append(s(tag, attrs));
  return el;
}

export function logoMark(size = 34) {
  return s("svg", { viewBox: "0 0 32 32", width: size, height: size, "aria-hidden": "true", class: "logo-mark" },
    s("path", { d: "M25.3 9.24A11.5 11.5 0 1 0 25.3 22.76", fill: "none", stroke: "currentColor", "stroke-width": 4.6, "stroke-linecap": "round" }),
    s("path", { d: "M15.6 11.6 22 16l-6.4 4.4z", fill: "currentColor" }));
}

// ------------------------------------------------------------------ api + live events
export function logout() {
  localStorage.removeItem("token");
  state.token = null;
  state.user = null;
  closeStreams();
  location.hash = "#/login";
}

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && !path.includes("/auth/")) {
    logout();
    throw new Error("Session expired - please sign in again");
  }
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
  }
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res.text();
}

let streams = [];
let leaveHooks = [];
export function closeStreams() { streams.forEach((es) => es.close()); streams = []; }
/** Subscribe to a server-sent-event stream; closed automatically when the page changes. */
export function sse(path, onEvent, { auth = true } = {}) {
  const url = auth && state.token ? `${path}${path.includes("?") ? "&" : "?"}token=${state.token}` : path;
  const es = new EventSource(url);
  es.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  streams.push(es);
  return es;
}
/** Register cleanup (timers, observers) to run when the user navigates away from the current page. */
export function onLeave(fn) { leaveHooks.push(fn); }
export function runLeave() { leaveHooks.forEach((fn) => { try { fn(); } catch { /* ignore */ } }); leaveHooks = []; closeStreams(); hideTip(); }
/** Re-render the current page. */
export function refresh() { window.dispatchEvent(new Event("app:refresh")); }

// ------------------------------------------------------------------ formatting
export const fmt = {
  ago(iso) {
    const sec = Math.max(0, (Date.now() - new Date(iso)) / 1000);
    if (sec < 60) return "just now";
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  },
  pct: (v, digits = 0) => (v == null ? "–" : `${(v * 100).toFixed(digits)}%`),
  num: (v) => (v == null ? "–" : Number(v).toLocaleString("en-GB")),
  gbp: (v) => `£${Number(v || 0).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
  usd: (v, digits = 4) => (v == null ? "–" : `$${Number(v).toFixed(digits)}`),
  ms(v) {
    if (v == null) return "–";
    return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`;
  },
  dateTime: (iso) => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }),
  day: (iso) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" }),
  weekday: (iso) => new Date(iso).toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" }),
  bytes: (n) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`),
};

// ------------------------------------------------------------------ small UI pieces
export function pill(text, kind = "") { return h("span", { class: `pill ${kind}`.trim() }, text); }

const AGENTS = { support: ["Support", "blue"], orders: ["Orders", "green"], escalation: ["Escalation", "violet"],
  router: ["Router", "grey"], system: ["System", "grey"], all: ["All agents", "grey"] };
export function agentPill(agent) {
  if (!agent) return null;
  if (agent.startsWith("human")) return pill("Human", "amber");
  const [label, kind] = AGENTS[agent] || [agent, "grey"];
  return pill(label, kind);
}

const STATUS_KIND = { open: "blue", escalated: "red", resolved: "grey", pending_review: "amber", pending_approval: "amber", sent: "green",
  rejected: "red", superseded: "grey", issued: "green", pending: "amber", running: "blue", done: "grey", dead: "red", claimed: "blue",
  critical: "red", high: "red", medium: "amber", low: "blue", processing: "amber", dispatched: "blue", in_transit: "blue",
  delivered: "green", cancelled: "grey", refunded: "violet", partially_refunded: "violet" };
export function statusPill(status) {
  const text = String(status || "").replace(/_/g, " ");
  return pill(text.charAt(0).toUpperCase() + text.slice(1), STATUS_KIND[status] || "grey");
}

export function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2)).toUpperCase();
}
export function avatar(name, cls = "") {
  const tints = ["green", "blue", "violet", "amber"];
  let hash = 0;
  for (const ch of String(name || "")) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return h("span", { class: `avatar ${tints[hash % tints.length]} ${cls}`.trim(), "aria-hidden": "true" }, initials(name));
}
export const channelIcon = (channel, size = 16) => icon({ web: "globe", email: "mail", telegram: "plane" }[channel] || "chat", size);
export const channelName = (channel) => ({ web: "Web chat", email: "Email", telegram: "Telegram" }[channel] || channel);

/** Card with the mockup's header: icon + title on the left, optional action on the right, hairline below. */
export function card({ title, iconName, action, body, cls = "", flush = false }) {
  return h("section", { class: `card ${cls}`.trim() },
    title ? h("header", { class: "card-head" }, iconName ? icon(iconName, 22, "card-icon") : null, h("h2", {}, title), action ? h("div", { class: "card-action" }, action) : null) : null,
    h("div", { class: flush ? "card-body flush" : "card-body" }, body));
}
export function linkArrow(label, href) { return h("a", { class: "link", href }, label, icon("arrowRight", 16)); }
export function empty(text, iconName = "inbox") { return h("div", { class: "empty" }, icon(iconName, 26), h("p", {}, text)); }
export function stat(label, value, sub) {
  return h("div", { class: "stat" }, h("div", { class: "stat-label" }, label), h("div", { class: "stat-value" }, value), sub ? h("div", { class: "stat-sub" }, sub) : null);
}
export function tabs(items, active, onSelect) {
  return h("div", { class: "tabs", role: "tablist" }, items.map(([id, label, count]) =>
    h("button", { class: id === active ? "tab on" : "tab", role: "tab", "aria-selected": id === active ? "true" : "false", onclick: () => onSelect(id) },
      label, count ? h("span", { class: "tab-count" }, count) : null)));
}
/** Data table. A header is a label, or [label, "num"] for a right-aligned numeric column. */
export function table(headers, rows, { emptyText = "Nothing here yet.", compact = false } = {}) {
  return h("div", { class: "table-wrap" }, h("table", { class: compact ? "table compact" : "table" },
    h("thead", {}, h("tr", {}, headers.map((x) => (Array.isArray(x) ? h("th", { class: x[1] }, x[0]) : h("th", {}, x))))),
    h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: headers.length, class: "muted" }, emptyText)))));
}
/** Screen-reader twin of a chart. Wrapped in a div because a bare <table> ignores the 1px clip box and would add scroll height. */
export function srTable(caption, headers, rows) {
  return h("div", { class: "sr-only" }, h("table", {}, h("caption", {}, caption), h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
}

export function toast(message, kind = "") {
  const el = h("div", { class: `toast ${kind}`.trim(), role: "status" }, icon(kind === "error" ? "alert" : "check", 18), message);
  document.body.append(el);
  setTimeout(() => el.remove(), 3600);
}

/** Modal dialog. An action's onclick may return false to keep the dialog open. */
export function modal({ title, body, actions = [], wide = false }) {
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey); };
  const overlay = h("div", { class: "overlay", onmousedown: (e) => { if (e.target === overlay) close(); } },
    h("div", { class: wide ? "modal wide" : "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("header", {}, h("h3", {}, title), h("button", { class: "iconbtn sm", "aria-label": "Close", onclick: close }, icon("x", 18))),
      h("div", { class: "modal-body" }, body),
      actions.length ? h("footer", {}, actions.map((a) => h("button", { class: `btn ${a.kind || ""}`.trim(), onclick: async (e) => {
        const button = e.currentTarget;
        button.disabled = true;
        let keepOpen = false;
        try { keepOpen = (await a.onclick?.()) === false; } catch (err) { keepOpen = true; toast(err.message, "error"); }
        if (keepOpen) button.disabled = false; else close();
      } }, a.label))) : null));
  document.body.append(overlay);
  document.addEventListener("keydown", onKey);
  overlay.querySelector("textarea, input, select")?.focus();
  return { close };
}

// ------------------------------------------------------------------ chart tooltip (one shared element)
let tip;
export function showTip(x, y, rows, title) {
  if (!tip) { tip = h("div", { class: "tooltip", role: "status" }); document.body.append(tip); }
  set(tip, title ? h("div", { class: "tt-title" }, title) : null,
    rows.map((r) => h("div", { class: "tt-row" }, r.color ? h("i", { class: "tt-key", style: `background:${r.color}` }) : null, h("b", {}, r.value), h("span", {}, r.label))));
  tip.style.display = "block";
  const pad = 12, w = tip.offsetWidth, ht = tip.offsetHeight;
  let left = x + pad, top = y - ht - pad;
  if (left + w > innerWidth - 8) left = x - w - pad;
  if (top < 8) top = y + pad;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${top}px`;
}
export function hideTip() { if (tip) tip.style.display = "none"; }
/** Give a chart mark a hover + keyboard-focus tooltip. `rows` is a function returning [{label, value, color}]. */
export function bindTip(el, rows, title) {
  el.setAttribute("tabindex", "0");
  el.addEventListener("pointermove", (e) => showTip(e.clientX, e.clientY, rows(), title));
  el.addEventListener("pointerleave", hideTip);
  el.addEventListener("focus", () => { const r = el.getBoundingClientRect(); showTip(r.left + r.width / 2, r.top, rows(), title); });
  el.addEventListener("blur", hideTip);
  return el;
}
