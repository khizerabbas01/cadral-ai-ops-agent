// Human approval queue: refunds over the auto-approval limit, AI replies held for review and open escalations.
import { api, card, empty, h, icon, pill, set, toast } from "../lib.js";
import { Shell, loading, refreshStatus } from "../shell.js";
import { approveReply, rejectReplyDialog } from "./review.js";

const KIND = { refund: ["alert", "amber"], reply: ["pencil", "blue"], escalation: ["users", "green"] };

/** One queue row with the actions that fit its type. `reload` re-reads the queue after a decision. */
export function approvalRow(item, reload) {
  const [iconName, tint] = KIND[item.type] || ["info", "grey"];
  const run = async (action, message) => {
    try {
      await action();
      if (message) toast(message);
    } catch (err) {
      toast(err.message, "error");
    }
    refreshStatus();
    reload();
  };
  const approve = (label, onclick) => h("button", { class: "btn approve", onclick }, icon("check", 17), label);
  const reject = (label, onclick) => h("button", { class: "btn reject", onclick }, icon("x", 17), label);

  let actions;
  if (item.type === "refund") {
    actions = item.can_decide
      ? [approve("Approve", () => run(() => api(`/api/refunds/${item.id}/approve`, { method: "POST" }), "Refund approved and issued")),
         reject("Reject", () => run(() => api(`/api/refunds/${item.id}/reject`, { method: "POST" }), "Refund rejected"))]
      : [pill("Needs an admin", "grey")];
  } else if (item.type === "reply") {
    actions = [approve("Approve", () => run(() => approveReply(item.id))),
               reject("Reject", () => rejectReplyDialog(item.id, () => { refreshStatus(); reload(); }))];
  } else {
    actions = [approve("Claim", () => run(() => api(`/api/escalations/${item.id}/claim`, { method: "POST" }), "Escalation assigned to you")),
               h("a", { class: "btn", href: item.link }, "Open")];
  }
  return h("div", { class: "approval" },
    h("div", { class: `tile sm ${tint}` }, icon(iconName, 22)),
    h("div", { class: "grow" },
      h("a", { class: "row-title clip", href: item.link, style: "display:block" }, item.title,
        item.more ? h("span", { class: "muted small", style: "font-weight:500" }, `  +${item.more} more`) : null),
      h("div", { class: "row-sub clip" }, item.subtitle)),
    h("div", { class: "actions" }, actions));
}

export async function Approvals(ctx) {
  const view = Shell({ page: "dashboard", title: "Human Approval Queue", subtitle: "Decisions the agents are not allowed to take alone" });
  set(view, loading());
  const body = h("div", { class: "stack" });

  async function load() {
    const items = await api("/api/approvals");
    if (ctx.stale()) return;
    const group = (type, title, iconName, hint) => {
      const rows = items.filter((i) => i.type === type);
      return card({ title: `${title} (${rows.length})`, iconName, flush: true,
        body: rows.length ? h("div", { class: "rows" }, rows.map((i) => approvalRow(i, load))) : empty(hint, "check") });
    };
    set(body,
      group("refund", "Refunds over the auto-approval limit", "alert", "No refunds are waiting for approval."),
      group("reply", "AI replies held for review", "pencil", "No replies are held. Turn on \"Hold every reply for human review\" in Settings to approve replies before they are sent."),
      group("escalation", "Open escalations", "users", "No open escalations."));
    set(view, h("div", { class: "page-head" }, h("div", {}, h("h2", {}, `${items.length} item${items.length === 1 ? "" : "s"} waiting`),
      h("p", {}, "Reply decisions are saved as feedback for the learning loop; refund decisions go to the audit log."))), body);
  }
  await load();
}
