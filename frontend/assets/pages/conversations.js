// Conversations: inbox list, live thread (tokens stream in over SSE) and the agent trace for the selected reply.
import { agentPill, api, avatar, channelIcon, channelName, empty, fmt, h, icon, pill, refresh, set, sse, statusPill, toast } from "../lib.js";
import { Shell, refreshStatus } from "../shell.js";
import { approveReply, editReplyDialog, rejectReplyDialog } from "./review.js";
import { stepList, traceSummary } from "./trace_view.js";

const FILTERS = [["all", "All"], ["open", "Open"], ["escalated", "Escalated"], ["review", "Needs review"], ["resolved", "Resolved"]];
const SAMPLE_EMAILS = [
  ["grace.davies@example.co.uk", "Grace Davies", "Casserole dish chipped", "Hello, my order UK-10011 arrived yesterday and the casserole lid is chipped. Could I get a refund please?"],
  ["leo.robinson@example.co.uk", "Leo Robinson", "Delivery question", "Hi, do you deliver furniture to the Scottish Highlands and how long does it take?"],
  ["mia.thompson@example.co.uk", "Mia Thompson", "DISGRACEFUL SERVICE", "This is the THIRD time I am writing. Nobody replies. I want to speak to a manager or I will dispute the charge with my bank!!!"],
];

export async function Conversations(ctx) {
  const selectedId = ctx.arg ? Number(ctx.arg) : null;
  const view = Shell({ page: "conversations", title: "Conversations", flush: true,
    subtitle: "Every customer message from web chat, email and Telegram, answered live by the agents" });
  const listEl = h("div", { class: "pane-scroll" });
  const filtersEl = h("div", { class: "cluster", style: "gap:.35rem;margin-top:.7rem" });
  const threadEl = h("section", { class: "pane thread" });
  const traceEl = h("aside", { class: "pane trace-pane" });
  let filter = sessionStorage.getItem("conversations.filter") || "all";
  let convs = [];

  set(view, h("div", { class: selectedId ? "inbox" : "inbox no-selection" },
    h("div", { class: "pane list-pane" },
      h("div", { class: "pane-head" },
        h("div", { class: "cluster" }, h("b", { class: "grow", style: "font-size:1.1rem" }, "Inbox"),
          h("button", { class: "btn sm", onclick: simulateEmail, title: "Inject a sample email exactly as the mailbox poller would" }, icon("mail", 15), "Simulate email")),
        filtersEl),
      listEl),
    threadEl, traceEl));

  function drawFilters() {
    set(filtersEl, FILTERS.map(([id, label]) => h("button", { class: id === filter ? "chip on" : "chip", onclick: () => {
      filter = id;
      sessionStorage.setItem("conversations.filter", id);
      drawFilters();
      drawList();
    } }, label)));
  }
  function drawList() {
    const rows = convs.filter((c) => filter === "all" || (filter === "review" ? c.pending_review : c.status === filter));
    set(listEl, rows.length ? rows.map((c) => {
      const name = c.customer?.name || "Guest";
      return h("a", { class: c.id === selectedId ? "conv on" : "conv", href: `#/conversations/${c.id}` },
        avatar(name, "sm"),
        h("div", { class: "grow" },
          h("div", { class: "cluster", style: "gap:.4rem;flex-wrap:nowrap" }, h("span", { class: "name clip grow" }, name), h("span", { class: "muted small nowrap" }, fmt.ago(c.updated_at))),
          h("div", { class: "preview clip" }, c.last_message || c.subject),
          h("div", { class: "cluster", style: "gap:.3rem;margin-top:.4rem" },
            h("span", { class: "muted", title: channelName(c.channel) }, channelIcon(c.channel, 15)), statusPill(c.status), agentPill(c.last_agent),
            c.customer?.tier === "vip" ? pill("VIP", "amber") : null, c.pending_review ? pill("Needs review", "amber") : null)));
    }) : empty(filter === "all" ? "No conversations yet. Open the customer chat or simulate an email." : "No conversations match this filter."));
  }
  async function loadList() {
    convs = await api("/api/conversations");
    if (!ctx.stale()) drawList();
  }
  async function simulateEmail() {
    const [from_email, name, subject, text] = SAMPLE_EMAILS[Math.floor(Math.random() * SAMPLE_EMAILS.length)];
    try {
      const res = await api("/api/demo/simulate-email", { method: "POST", body: { from_email, name, subject, text } });
      toast(`Email received from ${name}`);
      location.hash = `#/conversations/${res.conversation_id}`;
    } catch (err) {
      toast(err.message, "error");
    }
  }

  drawFilters();
  await loadList();
  if (ctx.stale()) return;
  sse("/api/stream", (event) => { if (event.type === "conversation_updated" || event.type === "escalation") { loadList(); refreshStatus(); } });

  if (!selectedId) {
    if (convs.length && innerWidth > 860) { // open the most recent conversation without adding a history entry
      history.replaceState(null, "", `#/conversations/${convs[0].id}`);
      refresh();
      return;
    }
    set(threadEl, empty("Select a conversation to read it."));
    return;
  }

  // ------------------------------------------------------------ thread + trace
  let data;
  try {
    data = await api(`/api/conversations/${selectedId}`);
  } catch {
    set(threadEl, empty("This conversation could not be found."));
    return;
  }
  if (ctx.stale()) return;
  let selectedTrace = data.traces.at(-1)?.id ?? null;
  let live = null; // text streamed so far for the reply being written
  let liveSteps = [];
  const headEl = h("div", { class: "pane-head" });
  const msgsEl = h("div", { class: "msgs" });
  const input = h("input", { type: "text", placeholder: "Reply as a human agent…", "aria-label": "Reply as a human agent",
    onkeydown: (e) => { if (e.key === "Enter") sendHuman(); } });
  set(threadEl, headEl, msgsEl, h("div", { class: "composer" }, input, h("button", { class: "btn primary", onclick: sendHuman }, icon("send", 16), "Send")));

  const after = () => { refreshStatus(); reload(); };
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
  async function act(path, message) {
    try {
      await api(path, { method: "POST" });
      toast(message);
    } catch (err) {
      toast(err.message, "error");
    }
    after();
  }
  async function sendHuman() {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    try {
      await api(`/api/conversations/${selectedId}/reply`, { method: "POST", body: { text } });
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function drawHead() {
    const c = data.conversation, name = c.customer?.name || "Guest";
    const open = data.escalations.filter((e) => e.status !== "resolved");
    set(headEl,
      h("div", { class: "cluster", style: "flex-wrap:nowrap" },
        avatar(name),
        h("div", { class: "grow" },
          h("div", { class: "cluster", style: "gap:.45rem" }, h("b", { style: "font-size:1.08rem" }, name), c.customer?.tier === "vip" ? pill("VIP", "amber") : null, statusPill(c.status)),
          h("div", { class: "muted small clip" }, `${c.customer?.email || "Not identified"} · ${channelName(c.channel)} · #${c.id}`)),
        c.status !== "resolved" ? h("button", { class: "btn sm", onclick: () => act(`/api/conversations/${c.id}/resolve`, "Conversation resolved") }, icon("check", 15), "Resolve") : null),
      open.map((e) => h("div", { class: "banner" },
        h("b", {}, `Escalated · ${e.severity} severity · ${e.reason}`),
        h("div", { style: "margin:.25rem 0 .55rem" }, e.summary),
        h("div", { class: "cluster" },
          e.status === "open" ? h("button", { class: "btn sm", onclick: () => act(`/api/escalations/${e.id}/claim`, "Escalation assigned to you") }, "Claim") : pill("Claimed", "blue"),
          h("button", { class: "btn sm", onclick: () => act(`/api/escalations/${e.id}/resolve`, "Escalation resolved") }, "Mark resolved")))));
  }

  function bubble(m) {
    const ai = m.role === "assistant";
    const reviewable = ai && !m.feedback && m.status !== "rejected" && m.status !== "superseded";
    const who = ai ? [icon("sparkle", 14), agentPill(m.agent)]
      : m.role === "human" ? [icon("users", 14), (m.agent || "").replace("human:", "") || "Human agent"] : "Customer";
    const el = h("div", { class: `bubble ${m.role} ${m.status}${ai && m.trace_id === selectedTrace ? " selected" : ""}` },
      h("div", { class: "bubble-meta" }, who, m.status !== "sent" ? statusPill(m.status) : null,
        m.feedback ? pill(`Human: ${m.feedback.action}`, m.feedback.action === "approve" ? "green" : "amber") : null,
        h("span", {}, fmt.ago(m.created_at))),
      h("div", { class: "text" }, m.content),
      m.citations?.length ? h("div", { class: "cites" }, m.citations.map((c) =>
        h("div", { class: "cite", title: c.snippet }, h("b", {}, `[${c.n}] `), `${c.title} — ${c.section} `, h("span", { class: "muted" }, c.doc)))) : null,
      reviewable ? h("div", { class: "bubble-actions" },
        h("button", { class: "btn sm approve", onclick: stop(async () => {
          try { await approveReply(m.id); } catch (err) { toast(err.message, "error"); }
          after();
        }) }, icon("check", 15), "Approve"),
        h("button", { class: "btn sm", onclick: stop(() => editReplyDialog(m, after)) }, icon("pencil", 15), "Edit"),
        h("button", { class: "btn sm reject", onclick: stop(() => rejectReplyDialog(m.id, after)) }, icon("x", 15), "Reject")) : null);
    if (ai && m.trace_id) el.addEventListener("click", () => { selectedTrace = m.trace_id; drawMsgs(false); drawTrace(); });
    return el;
  }
  function drawMsgs(scroll = true) {
    set(msgsEl, data.messages.map(bubble),
      live != null ? h("div", { class: "bubble assistant" }, h("div", { class: "bubble-meta" }, icon("sparkle", 14), "Agent is working…"), h("span", { class: "typing" }, live)) : null);
    if (scroll) msgsEl.scrollTop = msgsEl.scrollHeight;
  }
  function drawTrace() {
    const trace = data.traces.find((t) => t.id === selectedTrace);
    const steps = trace ? trace.steps : live != null ? liveSteps : [];
    set(traceEl,
      h("div", { class: "pane-head" }, h("div", { class: "cluster" }, icon("nodes", 20), h("b", { class: "grow", style: "font-size:1.08rem" }, "Agent trace"),
        trace ? h("a", { class: "link small", href: `#/traces/${trace.id}` }, "Open", icon("arrowRight", 14)) : null)),
      h("div", { class: "pane-scroll", style: "padding:1.1rem" }, h("div", { class: "stack" },
        trace ? traceSummary(trace) : null,
        steps.length ? stepList(steps) : h("p", { class: "muted" }, "Select an AI reply to see how it was produced."),
        h("div", {},
          h("div", { class: "subhead", style: "margin-top:0" }, "Shared memory"),
          h("div", { class: "stack tight small" },
            h("div", {}, h("b", {}, "Orders: "), data.orders.length
              ? data.orders.map((o) => `${o.id} (${o.status.replace(/_/g, " ")}, ${fmt.gbp(o.total_gbp)})`).join(" · ") : "none on record"),
            data.customer_notes ? h("pre", {}, data.customer_notes) : null,
            data.conversation.summary ? h("div", { class: "muted" }, h("b", {}, "Summary: "), data.conversation.summary) : null)))));
  }
  async function reload() {
    try {
      data = await api(`/api/conversations/${selectedId}`);
    } catch {
      return;
    }
    if (ctx.stale()) return;
    if (!data.traces.find((t) => t.id === selectedTrace)) selectedTrace = data.traces.at(-1)?.id ?? null;
    drawHead();
    drawMsgs();
    drawTrace();
    loadList();
  }

  drawHead();
  drawMsgs();
  drawTrace();
  sse(`/api/conversations/${selectedId}/stream`, (event) => {
    if (event.type === "typing") {
      live = "";
      liveSteps = [];
      selectedTrace = event.trace_id;
      drawMsgs();
      drawTrace();
    } else if (event.type === "token") {
      live = (live || "") + event.text;
      const el = msgsEl.querySelector(".typing");
      if (el) { el.textContent = live; msgsEl.scrollTop = msgsEl.scrollHeight; } else drawMsgs();
    } else if (event.type === "trace_step") {
      liveSteps.push(event.step);
      if (live == null) live = "";
      drawTrace();
    } else if (event.type === "message") {
      if (event.message.role !== "customer") live = null;
      reload();
    }
  });
}
