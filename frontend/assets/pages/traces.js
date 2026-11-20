// Agent Trace explorer: every handled message with its route, tools, latency and cost; select one to see each step.
import { agentPill, api, card, channelIcon, channelName, empty, fmt, h, icon, pill, set, table } from "../lib.js";
import { Shell, loading } from "../shell.js";
import { stepList, traceSummary } from "./trace_view.js";

const ROUTES = [["", "All agents"], ["support", "Support"], ["orders", "Orders"], ["escalation", "Escalation"]];

export async function Traces(ctx) {
  const view = Shell({ page: "traces", title: "Agent Trace", subtitle: "For every message: which agent handled it, which tools were called and why" });
  set(view, loading());
  let route = "", traces = [], selected = ctx.arg ? Number(ctx.arg) : null;
  const filtersEl = h("div", { class: "cluster", style: "gap:.4rem" });
  const tableEl = h("div");
  const detailEl = h("div", { class: "sticky" });

  function choose(id) {
    selected = id;
    history.replaceState(null, "", `#/traces/${id}`); // deep link without re-rendering the page
    drawTable();
    drawDetail();
  }
  function outcome(t) {
    if (t.error) return pill("Error", "red");
    return t.resolved_by_ai ? pill("Auto-resolved", "green") : pill("Needs a human", "amber");
  }
  function drawFilters() {
    set(filtersEl, ROUTES.map(([id, label]) => h("button", { class: id === route ? "chip on" : "chip", onclick: () => { route = id; load(); } }, label)));
  }
  function drawTable() {
    set(tableEl, table(["Customer message and tools called", "Agent", ["Latency", "num"], "Outcome"], traces.map((t) =>
      h("tr", { class: t.id === selected ? "clickable on" : "clickable", tabindex: 0, onclick: () => choose(t.id), onkeydown: (e) => { if (e.key === "Enter") choose(t.id); } },
        h("td", { style: "width:100%;max-width:0" }, // takes the remaining width and lets the text truncate
          h("div", { class: "cluster", style: "gap:.5rem;flex-wrap:nowrap" }, h("span", { class: "strong clip" }, t.customer), h("span", { class: "muted small nowrap" }, fmt.ago(t.created_at))),
          h("div", { class: "muted small clip" }, t.message),
          t.tools.length ? h("div", { class: "mono clip", style: "margin-top:.2rem" }, [...new Set(t.tools)].join(" → ")) : null),
        h("td", {}, agentPill(t.route)),
        h("td", { class: "num nowrap" }, fmt.ms(t.latency_ms)),
        h("td", { class: "nowrap" }, outcome(t)))), { emptyText: "No traces yet. Send a message from the customer chat.", compact: true }));
  }
  function drawDetail() {
    const t = traces.find((x) => x.id === selected);
    if (!t) {
      set(detailEl, card({ title: "Trace", iconName: "nodes", body: empty("Select a row to see how the reply was produced.", "nodes") }));
      return;
    }
    set(detailEl, card({ title: `Trace #${t.id}`, iconName: "nodes",
      action: h("a", { class: "link small", href: `#/conversations/${t.conversation_id}` }, "Open conversation", icon("arrowRight", 14)),
      body: h("div", { class: "stack" },
        h("div", {},
          h("div", { class: "cluster muted small", style: "gap:.4rem" }, channelIcon(t.channel, 15), `${t.customer} · ${channelName(t.channel)} · ${fmt.dateTime(t.created_at)}`),
          h("div", { class: "bubble customer", style: "max-width:100%;margin-top:.5rem" }, t.message)),
        traceSummary(t),
        stepList(t.steps),
        t.reply ? h("div", {}, h("div", { class: "subhead", style: "margin-top:0" }, "Reply sent"),
          h("div", { class: "bubble assistant", style: "max-width:100%;cursor:default" }, t.reply)) : null) }));
  }
  async function load() {
    drawFilters();
    traces = await api(`/api/traces?limit=80${route ? `&route=${route}` : ""}`);
    if (ctx.stale()) return;
    if (!traces.find((t) => t.id === selected)) selected = traces[0]?.id ?? null;
    drawTable();
    drawDetail();
  }

  await load();
  if (ctx.stale()) return;
  set(view,
    h("div", { class: "page-head" }, h("div", {}, h("h2", {}, "Recent traces"), h("p", {}, "Tokens, cost and latency are recorded for every request.")),
      h("div", { class: "right" }, filtersEl)),
    h("div", { class: "split" }, card({ flush: true, body: tableEl }), detailEl));
}
