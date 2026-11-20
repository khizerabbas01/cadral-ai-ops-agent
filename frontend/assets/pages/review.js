// Human-in-the-loop dialogs shared by the Conversations page and the approval queue.
// Every edit or rejection is stored as feedback and feeds the self-improving loop.
import { api, h, modal, toast } from "../lib.js";

export async function approveReply(messageId) {
  await api(`/api/messages/${messageId}/review`, { method: "POST", body: { action: "approve" } });
  toast("Reply approved · saved as a training example");
}

export function editReplyDialog(message, onDone) {
  const text = h("textarea", { rows: 7, value: message.content });
  const reason = h("input", { type: "text", placeholder: "e.g. Always mention the 8pm cut-off for next-day delivery" });
  modal({
    title: "Edit the AI reply", wide: true,
    body: [
      h("label", { class: "field" }, "Corrected reply", text, h("span", { class: "hint" }, "Sent to the customer and kept as a few-shot example for similar messages.")),
      h("label", { class: "field" }, "What was wrong? (optional)", reason, h("span", { class: "hint" }, "Turned into a guideline that is added to the agent's prompt.")),
    ],
    actions: [
      { label: "Cancel", kind: "ghost" },
      { label: "Save and send", kind: "primary", onclick: async () => {
        if (!text.value.trim()) { toast("The reply cannot be empty", "error"); return false; }
        await api(`/api/messages/${message.id}/review`, { method: "POST", body: { action: "edit", text: text.value, reason: reason.value } });
        toast("Reply edited · feedback saved");
        onDone?.();
      } },
    ],
  });
}

export function rejectReplyDialog(messageId, onDone) {
  const reason = h("textarea", { rows: 4, placeholder: "e.g. Never promise compensation before a team leader has reviewed the case" });
  modal({
    title: "Reject the AI reply",
    body: [h("label", { class: "field" }, "Why is this reply wrong?", reason,
      h("span", { class: "hint" }, "A held reply is not sent. The reason becomes a guideline in the agent's prompt."))],
    actions: [
      { label: "Cancel", kind: "ghost" },
      { label: "Reject reply", kind: "reject", onclick: async () => {
        await api(`/api/messages/${messageId}/review`, { method: "POST", body: { action: "reject", reason: reason.value } });
        toast("Reply rejected · feedback saved");
        onDone?.();
      } },
    ],
  });
}
