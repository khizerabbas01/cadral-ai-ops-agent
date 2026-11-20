// Analytics: auto-resolution, response time, cost per conversation and accuracy over time.
import { api, card, fmt, h, set, stat, table } from "../lib.js";
import { lineChart, rankedBars } from "../charts.js";
import { Shell, loading } from "../shell.js";

const AGENT = { support: "Support agent", orders: "Orders agent", escalation: "Escalation agent" };
const CHANNEL = { web: "Web chat", email: "Email", telegram: "Telegram" };
const statCard = (...args) => h("section", { class: "card" }, h("div", { class: "card-body" }, stat(...args)));

export async function Analytics(ctx) {
  const days = [7, 14, 30].includes(Number(ctx.query.get("days"))) ? Number(ctx.query.get("days")) : 14;
  const view = Shell({ page: "analytics", title: "Analytics", subtitle: "Tokens, cost and latency are tracked for every request and rolled up here" });
  set(view, loading());
  const a = await api(`/api/analytics?days=${days}`);
  if (ctx.stale()) return;
  const s = a.summary;
  const series = (key) => a.series.map((p) => ({ label: fmt.day(p.date), title: new Date(p.date).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }), value: p[key] }));
  const pct = (v) => fmt.pct(v);

  set(view,
    h("div", { class: "page-head" },
      h("div", {}, h("h2", {}, `Last ${days} days`), h("p", {}, `${fmt.num(s.messages_in)} messages in ${fmt.num(s.conversations)} conversations · ${fmt.num(s.escalations)} escalations`)),
      h("div", { class: "right" }, [7, 14, 30].map((d) => h("a", { class: d === days ? "chip on" : "chip", href: `#/analytics?days=${d}` }, `${d} days`)))),
    h("div", { class: "grid g4" },
      statCard("Auto-resolution", fmt.pct(s.auto_resolution_rate, 1), "Answered without a human"),
      statCard("Average response time", fmt.ms(s.latency_ms.avg), `95% of replies within ${fmt.ms(s.latency_ms.p95)}`),
      statCard("Cost per conversation", fmt.usd(s.cost_usd.per_conversation, 4), `${fmt.usd(s.cost_usd.total, 4)} in total · ${fmt.num(s.tokens)} tokens`),
      statCard("AI accuracy", fmt.pct(s.accuracy.human_agreement), s.accuracy.reviewed ? `${s.accuracy.approved} of ${s.accuracy.reviewed} reviewed replies approved unchanged` : "No replies reviewed by a human yet")),
    h("div", { class: "grid g2" },
      card({ title: "Auto-resolution over time", iconName: "check", body: lineChart(series("auto_resolution"), { max: 1, format: pct, caption: "Auto-resolution rate per day", unit: "auto-resolved" }) }),
      card({ title: "Accuracy over time", iconName: "target", body: lineChart(series("accuracy"), { max: 1, format: pct, caption: "Share of reviewed replies approved unchanged, per day", unit: "approved unchanged" }) }),
      card({ title: "Average response time", iconName: "clock", body: lineChart(series("avg_latency_ms"), { format: (v) => fmt.ms(v), caption: "Average response time per day", unit: "average" }) }),
      card({ title: "Cost per conversation", iconName: "pie", body: lineChart(series("cost_per_conversation"), { format: (v) => fmt.usd(v, 4), caption: "LLM cost per conversation per day", unit: "per conversation" }) })),
    h("div", { class: "grid g3" },
      card({ title: "Top issues", iconName: "bars", body: rankedBars(s.top_issues) }),
      card({ title: "Handled by", iconName: "layers", body: [rankedBars(Object.entries(s.by_agent).sort((x, y) => y[1] - x[1]).map(([k, v]) => [AGENT[k] || k, v])),
        h("div", { class: "subhead" }, "Channel"), rankedBars(Object.entries(s.by_channel).sort((x, y) => y[1] - x[1]).map(([k, v]) => [CHANNEL[k] || k, v]))] }),
      card({ title: "Refunds", iconName: "refresh", body: [stat("Issued by the Orders agent", fmt.gbp(s.refunds_issued.total_gbp), `${s.refunds_issued.count} refund${s.refunds_issued.count === 1 ? "" : "s"} · ${s.refunds_issued.pending_approval} awaiting approval`),
        h("div", { class: "subhead" }, "Human review"), h("dl", { class: "kv" },
          h("dt", {}, "Approved"), h("dd", {}, s.accuracy.approved), h("dt", {}, "Edited"), h("dd", {}, s.accuracy.edited), h("dt", {}, "Rejected"), h("dd", {}, s.accuracy.rejected))] })),
    card({ title: "Model usage", iconName: "cpu", flush: true, body: table(["Model", ["Calls", "num"], ["Average latency", "num"], ["Cost at list price", "num"]], a.models.map((m) => h("tr", {},
      h("td", { class: "mono" }, m.model), h("td", { class: "num" }, fmt.num(m.calls)), h("td", { class: "num" }, fmt.ms(m.avg_latency_ms)), h("td", { class: "num" }, fmt.usd(m.cost_usd, 5)))),
      { emptyText: "No LLM calls recorded in this period (offline mode makes none)." }) }));
}
