// Settings: behaviour an admin can change at runtime, plus a read-only view of models, retrieval and integrations.
import { api, card, fmt, h, icon, pill, set, table, toast } from "../lib.js";
import { Shell, loading } from "../shell.js";

const dot = (on) => h("span", { class: on ? "statusdot on" : "statusdot off" }, h("i"), on ? "Connected" : "Not configured");

export async function Settings(ctx) {
  const view = Shell({ page: "settings", title: "Settings", subtitle: "How the agents behave, and what they are connected to" });
  set(view, loading());
  const s = await api("/api/settings");
  if (ctx.stale()) return;
  const edit = s.can_edit;

  async function save(changes, message) {
    try {
      await api("/api/settings", { method: "PATCH", body: changes });
      toast(message);
    } catch (err) {
      toast(err.message, "error");
    }
    Settings(ctx);
  }
  const limit = h("input", { type: "number", min: 0, max: 10000, step: 10, value: s.behaviour.refund_auto_limit_gbp, disabled: !edit, style: "width:7.5rem", "aria-label": "Refund limit in pounds" });
  const review = s.behaviour.reply_mode === "review";

  set(view,
    edit ? null : h("div", { class: "note" }, icon("info", 18), h("span", {}, "You are signed in as an agent. Only admins can change these settings.")),
    card({ title: "Agent behaviour", iconName: "shield", body: [
      h("div", { class: "setting" },
        h("div", {}, h("b", {}, "Hold every reply for human review"),
          h("p", {}, review ? "On: replies wait in the Human Approval Queue until someone approves, edits or rejects them."
            : "Off: replies are sent immediately and can still be reviewed afterwards. Escalation holding messages are always sent.")),
        h("label", { class: "switch" }, h("input", { type: "checkbox", checked: review, disabled: !edit, "aria-label": "Hold every reply for human review",
          onchange: (e) => save({ reply_mode: e.target.checked ? "review" : "auto" }, e.target.checked ? "Replies will now wait for approval" : "Replies will now be sent automatically") }), h("i"))),
      h("div", { class: "setting" },
        h("div", {}, h("b", {}, "Refund limit without approval"), h("p", {}, "The Orders agent issues refunds up to this amount itself. Anything larger is queued for an admin.")),
        h("div", { class: "cluster", style: "flex-wrap:nowrap" }, h("span", { class: "strong" }, "£"), limit,
          edit ? h("button", { class: "btn", onclick: () => save({ refund_auto_limit_gbp: Number(limit.value) }, "Refund limit updated") }, "Save") : null)),
    ] }),
    h("div", { class: "grid g2" },
      card({ title: "Models", iconName: "cpu", action: s.llm.enabled ? pill("Live", "green") : pill("Offline fallback", "amber"), body: h("dl", { class: "kv" },
        h("dt", {}, "Provider"), h("dd", {}, s.llm.provider),
        h("dt", {}, "Answers and tool use"), h("dd", { class: "mono" }, s.llm.model),
        h("dt", {}, "Routing and summaries"), h("dd", { class: "mono" }, s.llm.fast_model),
        h("dt", {}, "Fine-tuned router"), h("dd", {}, s.llm.fine_tuned_router ? "Serving (CLASSIFIER_URL)" : "Not deployed"),
        h("dt", {}, "API key"), h("dd", {}, s.llm.enabled ? "Set in the environment" : "Not set - agents use deterministic fallbacks")) }),
      card({ title: "Retrieval", iconName: "book", body: h("dl", { class: "kv" },
        h("dt", {}, "Documents"), h("dd", {}, s.rag.documents != null ? `${fmt.num(s.rag.documents)} documents, ${fmt.num(s.rag.chunks)} passages` : "Indexing…"),
        h("dt", {}, "Vector model"), h("dd", { class: "mono" }, s.rag.embedder || "–"),
        h("dt", {}, "Reranker"), h("dd", { class: "mono" }, s.rag.reranker || "–"),
        h("dt", {}, "Backend mode"), h("dd", {}, s.rag.backend === "lite" ? "Lite (no ONNX models)" : "Auto (ONNX models when installed)"),
        h("dt", {}, "Confidence threshold"), h("dd", {}, `${s.rag.confidence_threshold} - below it the agent says "I don't know"`)) })),
    h("div", { class: "grid g2" },
      card({ title: "Integrations", iconName: "zap", body: [h("dl", { class: "kv" },
        h("dt", {}, "Slack alerts"), h("dd", {}, dot(s.integrations.slack)),
        h("dt", {}, "Outgoing email (SMTP)"), h("dd", {}, dot(s.integrations.smtp)),
        h("dt", {}, "Support inbox (IMAP)"), h("dd", {}, dot(s.integrations.imap)),
        h("dt", {}, "Telegram bot"), h("dd", {}, dot(s.integrations.telegram))),
        h("p", { class: "muted small", style: "margin-top:.9rem" }, "Credentials are read from environment variables and are never stored in the database or shown here.")] }),
      card({ title: "Workspace", iconName: "home", body: h("dl", { class: "kv" },
        h("dt", {}, "Name"), h("dd", {}, s.workspace.name),
        h("dt", {}, "Public address"), h("dd", { class: "mono" }, s.workspace.public_base_url),
        h("dt", {}, "Customer chat"), h("dd", {}, h("a", { href: "#/chat", target: "_blank", rel: "noopener" }, "Open the chat widget"))) })),
    s.users ? card({ title: "Team", iconName: "users", flush: true, body: table(["Name", "Email", "Role"], s.users.map((u) => h("tr", {},
      h("td", { class: "strong" }, u.name), h("td", {}, u.email), h("td", {}, pill(u.role === "admin" ? "Admin" : "Agent", u.role === "admin" ? "green" : "blue"))))) }) : null);
}
