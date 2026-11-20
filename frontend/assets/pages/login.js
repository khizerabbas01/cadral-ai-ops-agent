import { api, h, icon, logoMark, set, state } from "../lib.js";

export function Login() {
  const email = h("input", { type: "email", value: "admin@demo.co.uk", autocomplete: "username", required: true });
  const password = h("input", { type: "password", autocomplete: "current-password", required: true, placeholder: "Your password" });
  const error = h("div", { class: "note red", hidden: true });
  const button = h("button", { class: "btn primary", type: "submit", style: "height:2.8rem" }, "Sign in");

  async function submit(e) {
    e.preventDefault();
    button.disabled = true;
    try {
      const res = await api("/api/auth/login", { method: "POST", body: { email: email.value, password: password.value } });
      state.token = res.token;
      state.user = res.user;
      localStorage.setItem("token", res.token);
      location.hash = "#/dashboard";
      window.dispatchEvent(new Event("app:refresh"));
    } catch (err) {
      set(error, icon("alert", 18), h("span", {}, err.message));
      error.hidden = false;
      button.disabled = false;
    }
  }

  const feature = (text) => h("li", {}, icon("check", 18), text);
  set(document.getElementById("app"), h("div", { class: "login" },
    h("div", { class: "login-brand" },
      h("div", { class: "brand" }, logoMark(40), h("span", { class: "brand-name" }, "CADRAL ", h("b", {}, "AI"))),
      h("div", {},
        h("h1", {}, "Autonomous AI Operations Agent"),
        h("p", {}, "One AI system that answers customers, takes real actions on orders, hands risky cases to people and learns from every correction."),
        h("ul", {}, feature("Router, Support, Orders and Escalation agents with shared memory"),
          feature("Cited answers from the knowledge base - or an honest \"I don't know\""),
          feature("Human approval queue, agent traces and live analytics"))),
      h("div", { style: "color:#a9c9bd;font-size:.9rem" }, "Workspace · Harbour & Lane")),
    h("div", { class: "login-form" }, h("form", { onsubmit: submit },
      h("div", {}, h("h2", {}, "Sign in"), h("p", { class: "muted" }, "Use your admin or agent account.")),
      error,
      h("label", { class: "field" }, "Email", email),
      h("label", { class: "field" }, "Password", password),
      button,
      h("p", { class: "muted small" }, "Admins manage settings, refunds and automation. Agents review replies and handle escalations."),
      h("a", { href: "#/chat", class: "link" }, "Open the customer chat demo", icon("arrowRight", 16))))));
}
