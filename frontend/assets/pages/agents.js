// Agents: what each agent does and how it performed, plus the Escalation agent's queue and the Orders agent's database.
import { api, card, fmt, h, icon, pill, set, state, statusPill, table, tabs, toast } from "../lib.js";
import { COLORS, rankedBars } from "../charts.js";
import { Shell, loading, refreshStatus } from "../shell.js";

const TABS = [["overview", "Overview"], ["escalations", "Escalations"], ["orders", "Orders & refunds"]];

function agentCard({ name, tagline, iconName, tint, color, facts, sections }) {
  return h("section", { class: "card" }, h("div", { class: "card-body" },
    h("div", { class: "agent-head" },
      h("div", { class: `tile ${tint || ""}`, style: color ? `background:${color}1f;color:${color}` : null }, icon(iconName, 28)),
      h("div", { class: "grow" }, h("h3", {}, name), h("p", {}, tagline))),
    h("div", { class: "facts" }, facts.map(([value, label]) => h("div", {}, h("b", {}, value), h("span", {}, label)))),
    sections));
}
const section = (title, ...body) => [h("div", { class: "subhead" }, title), ...body];

async function Overview(view, ctx) {
  const a = await api("/api/agents?days=7");
  if (ctx.stale()) return;
  const node = (title, text) => h("div", { class: "flow-node" }, h("b", {}, title), h("span", {}, text));
  const arrow = () => h("div", { class: "flow-arrow" }, icon("arrowRight", 18));
  const severity = ["critical", "high", "medium", "low"].filter((s) => a.escalation.by_severity[s]).map((s) => [s[0].toUpperCase() + s.slice(1), a.escalation.by_severity[s]]);

  set(view,
    card({ title: "How a message flows", iconName: "route", body: h("div", { class: "flow" },
      node("1 · Channels", "Web chat, email, Telegram"), arrow(),
      node("2 · Router", "Picks the specialist, checks risk"), arrow(),
      node("3 · Specialist", "Answers from docs or acts with tools"), arrow(),
      node("4 · Reply + trace", "Sent, or held for approval"), arrow(),
      node("5 · Learning", "Human edits improve the next answer")) }),
    h("div", { class: "grid g2" },
      agentCard({ name: "Router agent", tagline: "Reads every message and sends it to the right specialist", iconName: "route", tint: "grey",
        facts: [[fmt.num(a.router.handled), "Messages routed (7 days)"], [a.router.avg_confidence == null ? "–" : fmt.pct(a.router.avg_confidence), "Average confidence"], [fmt.num(a.router.guardrail_overrides), "Safety overrides"]],
        sections: [section("Model", h("div", { class: "mono" }, a.router.model)),
          section("How routing decisions were made", rankedBars(a.router.methods, { emptyText: "No messages routed yet" })),
          h("p", { class: "muted small", style: "margin-top:.8rem" }, "Legal threats, chargebacks, safety incidents, abuse and prompt-injection attempts always go to a human, whatever the model says.")] }),
      agentCard({ name: "Support agent (RAG)", tagline: "Answers from company documents, with citations", iconName: "book", color: COLORS.support,
        facts: [[fmt.num(a.support.handled), `Handled · ${fmt.pct(a.support.share)}`], [fmt.pct(a.support.auto_resolved), "Auto-resolved"], [fmt.ms(a.support.avg_latency_ms), "Average latency"]],
        sections: [section("Model", h("div", { class: "mono" }, a.support.model)),
          section("Most cited documents", rankedBars(a.support.top_documents, { emptyText: "No cited answers yet", color: COLORS.support })),
          h("p", { class: "muted small", style: "margin-top:.8rem" }, `Said "I don't know" ${a.support.idk} time${a.support.idk === 1 ? "" : "s"} instead of guessing. `,
            h("a", { href: "#/knowledge" }, "Open the knowledge base"))] }),
      agentCard({ name: "Orders agent", tagline: "Checks, refunds, cancels and updates orders through real function calls", iconName: "box", color: "#2f9d6e",
        facts: [[fmt.num(a.orders.handled), `Handled · ${fmt.pct(a.orders.share)}`], [fmt.pct(a.orders.auto_resolved), "Auto-resolved"], [fmt.gbp(a.orders.refund_limit_gbp), "Refund limit without approval"]],
        sections: [section("Model", h("div", { class: "mono" }, a.orders.model)),
          section("Tools (calls in the last 7 days)", h("div", {}, a.orders.tools.map((t) => h("div", { class: "toolrow" },
            h("span", { class: "mono strong" }, t.name),
            h("span", { class: "cluster", style: "gap:.3rem" }, pill(`${t.calls} call${t.calls === 1 ? "" : "s"}`, t.calls ? "green" : "grey"), t.failures ? pill(`${t.failures} declined`, "amber") : null),
            h("span", { class: "desc" }, t.description)))))] }),
      agentCard({ name: "Escalation agent", tagline: "Detects angry or high-risk customers and hands them to a person with a summary", iconName: "headset", color: "#7466d9",
        facts: [[fmt.num(a.escalation.handled), `Handled · ${fmt.pct(a.escalation.share)}`], [fmt.num(a.escalation.open), "Open right now"], [fmt.ms(a.escalation.avg_latency_ms), "Average latency"]],
        sections: [section("Model", h("div", { class: "mono" }, a.escalation.model)),
          section("Severity of handoffs (7 days)", rankedBars(severity, { emptyText: "No escalations in this period", color: COLORS.escalation })),
          h("p", { class: "muted small", style: "margin-top:.8rem" }, "Every handoff raises a Slack alert. The AI stays out of the conversation until a person resolves it. ",
            h("a", { href: "#/agents/escalations" }, "Open the queue"))] })),
    h("div", { class: "note" }, icon("brain", 18), h("span", {}, h("b", {}, "Shared memory and learning. "),
      `All agents read the same conversation history, customer profile, orders and notes. ${a.learning.active_rules} learned guideline${a.learning.active_rules === 1 ? " is" : "s are"} active and human-approved examples were injected ${a.learning.examples_injected} time${a.learning.examples_injected === 1 ? "" : "s"} in the last 7 days. `,
      h("a", { href: "#/evaluations/learning" }, "See the learning loop"))));
}

async function Escalations(view, ctx) {
  const rows = await api("/api/escalations");
  if (ctx.stale()) return;
  set(view, card({ title: "Escalations", iconName: "headset", flush: true,
    body: table(["Severity", "Reason", "Summary for the human", "Status", "Opened", ""], rows.map((e) => h("tr", {},
      h("td", {}, statusPill(e.severity)), h("td", {}, e.reason), h("td", { style: "max-width:34rem" }, e.summary), h("td", {}, statusPill(e.status)),
      h("td", { class: "nowrap muted" }, fmt.ago(e.created_at)),
      h("td", {}, h("a", { class: "btn sm", href: `#/conversations/${e.conversation_id}` }, "Open")))), { emptyText: "No escalations yet." }) }));
}

async function Orders(view, ctx) {
  const [orders, refunds, audit] = await Promise.all([api("/api/orders"), api("/api/refunds"), api("/api/audit")]);
  if (ctx.stale()) return;
  const admin = state.user.role === "admin";
  const decide = async (id, action) => {
    try {
      await api(`/api/refunds/${id}/${action}`, { method: "POST" });
      toast(action === "approve" ? "Refund approved and issued" : "Refund rejected");
    } catch (err) {
      toast(err.message, "error");
    }
    refreshStatus();
    Orders(view, ctx);
  };
  set(view,
    h("div", { class: "grid g2" },
      card({ title: "Refunds", iconName: "refresh", flush: true, body: table(["Order", ["Amount", "num"], "Status", "Issued by", ""], refunds.map((r) => h("tr", { title: r.reason },
        h("td", { class: "mono" }, r.order_id), h("td", { class: "num" }, fmt.gbp(r.amount_gbp)), h("td", {}, statusPill(r.status)), h("td", { class: "muted small" }, r.issued_by),
        h("td", {}, r.status === "pending_approval"
          ? (admin ? h("div", { class: "cluster", style: "gap:.4rem;flex-wrap:nowrap" },
              h("button", { class: "btn sm approve", onclick: () => decide(r.id, "approve") }, "Approve"), h("button", { class: "btn sm reject", onclick: () => decide(r.id, "reject") }, "Reject"))
            : pill("Needs an admin", "grey")) : null))), { emptyText: "No refunds yet." }) }),
      card({ title: "Audit log", iconName: "shield", flush: true, body: table(["When", "Action", "Record", "Actor"], audit.slice(0, 12).map((a) => h("tr", { title: JSON.stringify(a.details) },
        h("td", { class: "nowrap muted" }, fmt.ago(a.created_at)), h("td", {}, a.action.replace(/_/g, " ")), h("td", { class: "mono" }, a.entity_id), h("td", { class: "muted small" }, a.actor))),
        { emptyText: "Every tool write will be listed here." }) })),
    card({ title: "Orders database", iconName: "database", flush: true, body: table(["Order", "Customer", "Items", ["Total", "num"], ["Refunded", "num"], "Status"], orders.map((o) => h("tr", {},
      h("td", { class: "mono" }, o.id), h("td", {}, o.customer), h("td", { class: "small" }, o.items.map((i) => `${i.qty} × ${i.name}`).join(", ")),
      h("td", { class: "num" }, fmt.gbp(o.total_gbp)), h("td", { class: "num" }, o.refunded_gbp ? fmt.gbp(o.refunded_gbp) : "–"), h("td", {}, statusPill(o.status))))) }));
}

export async function Agents(ctx) {
  const tab = TABS.some(([id]) => id === ctx.arg) ? ctx.arg : "overview";
  const view = Shell({ page: "agents", title: "Agents", subtitle: "A router and three specialists that share one memory of every customer" });
  const body = h("div", { class: "stack" }, loading());
  set(view, tabs(TABS, tab, (id) => { location.hash = `#/agents/${id}`; }), body);
  await { overview: Overview, escalations: Escalations, orders: Orders }[tab](body, ctx);
}
