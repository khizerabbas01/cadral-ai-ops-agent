// Entry point: hash router. Every page is an ES module under ./pages - there is no build step.
import { api, h, icon, runLeave, set, state } from "./lib.js";
import { Shell } from "./shell.js";
import { Agents } from "./pages/agents.js";
import { Analytics } from "./pages/analytics.js";
import { Approvals } from "./pages/approvals.js";
import { Automation } from "./pages/automation.js";
import { CustomerChat } from "./pages/chat.js";
import { Conversations } from "./pages/conversations.js";
import { Dashboard } from "./pages/dashboard.js";
import { Evaluations } from "./pages/evaluations.js";
import { Knowledge } from "./pages/knowledge.js";
import { Login } from "./pages/login.js";
import { Settings } from "./pages/settings.js";
import { Traces } from "./pages/traces.js";

const PAGES = { dashboard: Dashboard, conversations: Conversations, agents: Agents, knowledge: Knowledge, automation: Automation,
  evaluations: Evaluations, analytics: Analytics, traces: Traces, settings: Settings, approvals: Approvals };

let generation = 0;

async function render() {
  runLeave(); // close live streams, timers and observers of the page being left
  const mine = ++generation;
  const [, rawPage = "", rawArg = ""] = location.hash.split("/");
  const page = rawPage.split("?")[0] || "dashboard";
  const ctx = { arg: rawArg.split("?")[0] || null, query: new URLSearchParams(location.hash.split("?")[1] || ""), stale: () => mine !== generation };

  if (page === "chat") return CustomerChat();
  if (!state.token) return Login();
  if (!state.user) {
    try { state.user = await api("/api/me"); } catch { return Login(); }
  }
  if (page === "login") { location.hash = "#/dashboard"; return; }
  try {
    await (PAGES[page] || Dashboard)(ctx);
  } catch (err) {
    console.error(err);
    if (ctx.stale() || !state.token) return;
    const view = Shell({ page, title: "Something went wrong", subtitle: "The page could not be loaded" });
    set(view, h("div", { class: "note red" }, icon("alert", 18), h("span", {}, err.message || String(err))));
  }
}

window.addEventListener("hashchange", render);
window.addEventListener("app:refresh", render);
render();
