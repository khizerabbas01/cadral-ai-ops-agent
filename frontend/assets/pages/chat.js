// Public customer chat widget (no login): the web-chat channel. Replies stream in token by token.
import { api, h, icon, set, sse } from "../lib.js";

const CUSTOMERS = ["emma.clarke", "james.patel", "sophie.turner", "oliver.hughes", "amelia.wright", "harry.evans", "isla.roberts", "george.walker"];
const PROMPTS = ["How much is next-day delivery?", "Where is my order UK-10002?", "Can I return an opened pillow?",
  "My order arrived damaged, I'd like a refund", "I want to speak to a manager NOW"];
const titleCase = (slug) => slug.split(".").map((p) => p[0].toUpperCase() + p.slice(1)).join(" ");

export function CustomerChat() {
  const saved = JSON.parse(sessionStorage.getItem("chat") || "{}");
  let conversation = saved.conversation, token = saved.token, live = null, stream = null;
  const messages = [];
  const list = h("div", { class: "msgs", "aria-live": "polite" });
  const who = h("select", { "aria-label": "Demo customer", value: saved.email || "" },
    h("option", { value: "" }, "Guest (not signed in)"), CUSTOMERS.map((c) => h("option", { value: `${c}@example.co.uk` }, titleCase(c))));
  const input = h("input", { type: "text", placeholder: "Type your message…", "aria-label": "Message", onkeydown: (e) => { if (e.key === "Enter") send(); } });

  function draw() {
    set(list,
      h("div", { class: "bubble them" }, "Hi! I'm the Harbour & Lane assistant. Ask me about delivery, returns, products or your order."),
      messages.map((m) => h("div", { class: m.role === "customer" ? "bubble me" : "bubble them" }, m.content,
        m.citations?.length ? h("div", { class: "cites" }, m.citations.map((c) => h("div", { class: "cite" }, h("b", {}, `[${c.n}] `), `${c.title} — ${c.section}`))) : null)),
      live != null ? h("div", { class: "bubble them" }, h("span", { class: "typing" }, live)) : null);
    list.scrollTop = list.scrollHeight;
  }
  function listen() {
    if (stream || !conversation) return;
    stream = sse(`/api/chat/${conversation}/stream?token=${token}`, (event) => {
      if (event.type === "typing") live = "";
      else if (event.type === "token") live = (live || "") + event.text;
      else if (event.type === "message" && event.message.role !== "customer") {
        live = null;
        if (!messages.find((m) => m.id === event.message.id)) messages.push(event.message);
      }
      draw();
    }, { auth: false }); // the chat token in the URL is scoped to this one conversation
  }
  async function send(text) {
    text = (text || input.value).trim();
    if (!text) return;
    input.value = "";
    messages.push({ role: "customer", content: text });
    live = "";
    draw();
    try {
      const res = await api("/api/chat/send", { method: "POST", body: { text, conversation_id: conversation, token, email: who.value || null } });
      conversation = res.conversation_id;
      token = res.token;
      sessionStorage.setItem("chat", JSON.stringify({ conversation, token, email: who.value }));
      listen();
    } catch (err) {
      live = null;
      messages.push({ role: "assistant", content: `Sorry, that did not go through: ${err.message}` });
      draw();
    }
  }
  who.addEventListener("change", () => { // switching customer starts a fresh conversation
    sessionStorage.removeItem("chat");
    conversation = token = null;
    stream?.close();
    stream = null;
    messages.length = 0;
    live = null;
    draw();
  });

  set(document.getElementById("app"), h("div", { class: "chatpage" }, h("div", { class: "widget" },
    h("header", {}, h("b", {}, "Harbour & Lane"), h("span", {}, "AI assistant, supervised by our team · replies in seconds"), who),
    list,
    h("div", { class: "chips" }, PROMPTS.map((p) => h("button", { class: "chip", onclick: () => send(p) }, p))),
    h("div", { class: "composer" }, input, h("button", { class: "btn primary", onclick: () => send(), "aria-label": "Send" }, icon("send", 16), "Send")))));

  if (conversation) {
    api(`/api/chat/${conversation}/messages?token=${token}`).then((history) => { messages.push(...history); draw(); listen(); })
      .catch(() => { sessionStorage.removeItem("chat"); conversation = token = null; });
  }
  draw();
}
