// Dashboard home: KPI tiles, agent split, live conversations, human approval queue and conversation volume.
// One date-range control at the top scopes every number below it.
import { agentPill, api, avatar, card, channelName, empty, fmt, h, icon, linkArrow, onLeave, pill, set, sse, state } from "../lib.js";
import { COLORS, columnChart, donut, sparkline } from "../charts.js";
import { Shell, loading } from "../shell.js";
import { approvalRow } from "./approvals.js";

function deltaText(k) {
  const size = Math.abs(k.delta), sign = k.delta > 0 ? "+" : k.delta < 0 ? "-" : "";
  if (k.unit === "%") return `${sign}${size >= 10 ? Math.round(size) : size.toFixed(1)}%`;
  if (k.unit === "pp") return `${sign}${size.toFixed(1)} pp`;
  return `${sign}${size.toFixed(2)}`;
}

/** Stat tile: label, value, trend line and the change against the previous period (green when the change is good). */
function kpiTile({ iconName, label, value, k, versus }) {
  let delta;
  if (k.delta == null) {
    delta = [h("b", { class: "flat" }, "–"), "no earlier data to compare"];
  } else {
    const up = k.delta > 0, tone = k.delta === 0 ? "flat" : up === k.higher_is_better ? "good" : "bad";
    delta = [h("b", { class: tone }, k.delta === 0 ? null : icon(up ? "arrowUp" : "arrowDown", 15), deltaText(k)), versus];
  }
  return h("section", { class: "card kpi" },
    h("div", { class: "tile" }, icon(iconName, 30)),
    h("div", { class: "kpi-label" }, label),
    h("div", { class: "kpi-row" }, h("div", { class: "kpi-value" }, value), sparkline(k.spark)),
    h("div", { class: "kpi-delta" }, delta));
}

export async function Dashboard(ctx) {
  const view = Shell({ page: "dashboard", title: "Autonomous AI Operations Agent",
    subtitle: "Multi-Agent Operations • RAG • Fine-Tuning • Automation • Monitoring" });
  let days = [7, 14, 30].includes(Number(localStorage.getItem("dashboard.days"))) ? Number(localStorage.getItem("dashboard.days")) : 7;
  const body = h("div", { class: "stack" }, loading());
  const sample = h("span", { class: "sample-note", hidden: true, title: "Sample messages replayed through the pipeline in offline mode (no LLM calls) so the charts have a history. New conversations are answered live." },
    icon("info", 15), "Includes replayed sample traffic");
  const range = h("select", { "aria-label": "Date range", value: String(days), onchange: () => {
    days = Number(range.value);
    localStorage.setItem("dashboard.days", String(days));
    load();
  } }, [7, 14, 30].map((d) => h("option", { value: String(d) }, `Last ${d} Days`)));

  set(view,
    h("div", { class: "page-head" },
      h("div", {}, h("h2", {}, `Welcome back, ${state.user.name.split(" ")[0]} 👋`), h("p", {}, "Here's how your AI operations are performing today.")),
      h("div", { class: "right" }, sample, h("label", { class: "select" }, icon("calendar", 20), range, icon("chevronDown", 18)))),
    body);

  function draw(d) {
    const versus = `vs. previous ${d.days} days`, k = d.kpis;
    const segments = d.agents.segments.map((seg) => ({ label: seg.label, value: seg.count, color: COLORS[seg.key] }));
    const dateLabel = (date) => new Date(date).toLocaleDateString("en-GB", d.days <= 7 ? { weekday: "short", timeZone: "UTC" } : { day: "numeric", month: "short", timeZone: "UTC" });
    const dateTitle = (date) => new Date(date).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
    sample.hidden = !d.demo_data;

    set(body,
      h("div", { class: "grid g4" },
        kpiTile({ iconName: "chat", label: "Total Conversations", value: fmt.num(k.conversations.value), k: k.conversations, versus }),
        kpiTile({ iconName: "check", label: "Auto-Resolution", value: k.auto_resolution.value == null ? "–" : `${(k.auto_resolution.value * 100).toFixed(1)}%`, k: k.auto_resolution, versus }),
        kpiTile({ iconName: "clock", label: "Avg. Response Time", value: fmt.ms(k.response_time_ms.value), k: k.response_time_ms, versus }),
        kpiTile({ iconName: "target", label: "Evaluation Score", value: k.eval_score.value == null ? "–" : k.eval_score.value.toFixed(2), k: k.eval_score, versus: "vs. previous eval run" })),

      h("div", { class: "dash-row" },
        card({ title: "Multi-Agent Overview", iconName: "nodes", cls: "snug", action: linkArrow("View Agents", "#/agents"),
          body: d.agents.total
            ? h("div", { class: "donut-wrap" },
                donut(segments, { centerValue: fmt.num(d.agents.total), centerLabel: "Conversations" }),
                h("div", { class: "legend" }, segments.map((seg) => h("div", { class: "legend-row" },
                  h("i", { style: `background:${seg.color}` }), h("span", { class: "clip" }, seg.label),
                  h("span", { class: "fig" }, h("b", {}, fmt.pct(seg.value / d.agents.total)), ` (${fmt.num(seg.value)})`)))))
            : empty("No conversations in this period yet.") }),
        card({ title: "Live Conversations", iconName: "chat", action: linkArrow("View All", "#/conversations"), flush: true,
          body: d.live.length
            ? h("div", { class: "rows" }, d.live.slice(0, 4).map((c) => h("a", { class: "row-item", href: `#/conversations/${c.id}`, title: `${c.customer} · ${channelName(c.channel)}` },
                avatar(c.customer),
                h("div", { class: "grow" }, h("div", { class: "row-title clip" }, c.topic), h("div", { class: "row-sub clip" }, c.preview)),
                agentPill(c.agent) || pill("Queued", "grey"),
                h("span", { class: "when" }, fmt.ago(c.updated_at)))))
            : empty("No conversations yet. Open the customer chat to start one.") })),

      h("div", { class: "dash-row wide-left" },
        card({ title: "Human Approval Queue", iconName: "nodes", flush: true,
          action: [d.approvals.total ? pill(String(d.approvals.total), "amber") : null, linkArrow("View All", "#/approvals")],
          body: d.approvals.items.length
            ? h("div", { class: "rows" }, d.approvals.items.map((item) => approvalRow(item, load)))
            : empty("Nothing is waiting for a human right now.", "check") }),
        card({ title: "Conversation Volume", iconName: "bars", cls: "snug",
          body: columnChart(d.volume.map((v) => ({ label: dateLabel(v.date), title: dateTitle(v.date), value: v.conversations })),
            { heightRem: 10.3, caption: `Conversations per day, last ${d.days} days`, unit: "conversations" }) })));
  }

  async function load() {
    view.classList.add("loading"); // keep the previous frame while refetching - no layout jump
    try {
      const data = await api(`/api/dashboard?days=${days}`);
      if (!ctx.stale()) draw(data);
    } finally {
      view.classList.remove("loading");
    }
  }

  await load();
  if (ctx.stale()) return;
  let timer = null;
  sse("/api/stream", (event) => {
    if (event.type !== "conversation_updated" && event.type !== "escalation") return;
    clearTimeout(timer);
    timer = setTimeout(load, 1200);
  });
  onLeave(() => clearTimeout(timer));
}
