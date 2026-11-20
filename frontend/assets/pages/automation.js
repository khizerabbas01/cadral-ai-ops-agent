// Automation: inbound channels, Slack alerts, the scheduled management report and the durable job queue.
import { api, card, fmt, h, icon, pill, set, state, statusPill, table, toast } from "../lib.js";
import { Shell, loading, refreshStatus } from "../shell.js";

const CHANNEL_ICON = { web: "globe", email: "mail", telegram: "plane" };
const dot = (on, onText = "Connected", offText = "Not configured") => h("span", { class: on ? "statusdot on" : "statusdot off" }, h("i"), on ? onText : offText);

export async function Automation(ctx) {
  const view = Shell({ page: "automation", title: "Automation", subtitle: "Channels, alerts, scheduled reports and a job queue that never loses a message" });
  set(view, loading());
  const admin = state.user.role === "admin";
  const [auto, report, jobs] = await Promise.all([api("/api/automation"), api("/api/admin/report/preview"), admin ? api("/api/jobs") : Promise.resolve([])]);
  if (ctx.stale()) return;
  const counts = auto.queue.counts;

  async function sendReport(e) {
    e.currentTarget.disabled = true;
    try {
      const r = await api("/api/admin/report/send", { method: "POST" });
      toast(auto.report.smtp && auto.report.recipients ? `Report queued (job #${r.queued_job})` : `Report job #${r.queued_job} queued - it will be skipped until SMTP and recipients are configured`);
    } catch (err) {
      toast(err.message, "error");
    }
    Automation(ctx);
  }
  async function replay(id) {
    try {
      await api(`/api/jobs/${id}/retry`, { method: "POST" });
      toast(`Job #${id} queued again`);
    } catch (err) {
      toast(err.message, "error");
    }
    refreshStatus();
    Automation(ctx);
  }

  set(view,
    h("div", { class: "grid g3" }, auto.channels.map((c) => h("section", { class: "card" }, h("div", { class: "card-body" },
      h("div", { class: "agent-head" }, h("div", { class: "tile" }, icon(CHANNEL_ICON[c.key], 28)),
        h("div", { class: "grow" }, h("h3", {}, c.label), h("p", {}, `${fmt.num(c.conversations_7d)} conversation${c.conversations_7d === 1 ? "" : "s"} in the last 7 days`))),
      h("dl", { class: "kv", style: "margin:1rem 0 .8rem" },
        h("dt", {}, "Receiving"), h("dd", {}, dot(c.inbound, "Connected", c.key === "email" ? "Webhook only" : "Not configured")),
        h("dt", {}, "Replying"), h("dd", {}, dot(c.outbound))),
      h("p", { class: "muted small" }, c.detail))))),

    h("div", { class: "grid g2" },
      card({ title: "Slack alerts", iconName: "hash", action: dot(auto.alerts.slack),
        body: [h("p", { class: "muted", style: "margin-bottom:.7rem" }, "A message is posted to the team channel for:"),
          h("ul", { style: "margin:0;padding-left:1.2rem;display:grid;gap:.35rem" }, auto.alerts.triggers.map((t) => h("li", {}, t))),
          auto.alerts.slack ? null : h("div", { class: "note amber", style: "margin-top:1rem" }, icon("info", 18), h("span", {}, "Set SLACK_WEBHOOK_URL to turn alerts on. Until then alert jobs are recorded and skipped."))] }),
      card({ title: "Daily management report", iconName: "calendar",
        action: admin ? h("button", { class: "btn sm primary", onclick: sendReport }, icon("send", 15), "Send now") : null,
        body: h("dl", { class: "kv" },
          h("dt", {}, "Schedule"), h("dd", {}, `Every day at ${String(auto.report.hour).padStart(2, "0")}:00 (${auto.report.timezone})`),
          h("dt", {}, "Next run"), h("dd", {}, auto.report.next_run
            ? `${new Date(auto.report.next_run).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/London" })} UK time`
            : "Scheduler is off"),
          h("dt", {}, "Recipients"), h("dd", {}, auto.report.recipients ? `${auto.report.recipients} address${auto.report.recipients === 1 ? "" : "es"}` : "None set (REPORT_RECIPIENTS)"),
          h("dt", {}, "Email delivery"), h("dd", {}, dot(auto.report.smtp)),
          h("dt", {}, "Contents"), h("dd", {}, "Volume, top issues, refunds issued, AI accuracy, response time and cost")) })),

    card({ title: "Report preview (last 24 hours)", iconName: "mail",
      body: h("iframe", { class: "report", sandbox: "", srcdoc: report.html, title: "Daily report preview" }) }),

    card({ title: "Job queue", iconName: "zap", flush: true,
      action: h("div", { class: "cluster", style: "gap:.35rem" }, ["pending", "running", "done", "dead"].map((s) => pill(`${counts[s] || 0} ${s === "dead" ? "failed" : s}`, s === "dead" && counts[s] ? "red" : s === "pending" && counts[s] ? "amber" : "grey"))),
      body: [h("div", { style: "padding:1rem 1.4rem;border-bottom:1px solid var(--line)" }, h("div", { class: "note" }, icon("shield", 18), h("span", {}, auto.queue.policy))),
        admin
          ? table(["Job", "Type", "Status", "Attempts", "Last error", ""], jobs.slice(0, 20).map((j) => h("tr", {},
              h("td", { class: "muted" }, `#${j.id}`), h("td", { class: "mono" }, j.kind), h("td", {}, statusPill(j.status)), h("td", {}, `${j.attempts} of ${j.max_attempts}`),
              h("td", { class: "muted small", style: "max-width:28rem" }, (j.last_error || "").split("\n")[0]),
              h("td", {}, j.status === "dead" ? h("button", { class: "btn sm", onclick: () => replay(j.id) }, icon("refresh", 14), "Replay") : null))), { emptyText: "No jobs yet." })
          : h("p", { class: "muted", style: "padding:1rem 1.4rem" }, "The job list is visible to admins.")] }));
}
