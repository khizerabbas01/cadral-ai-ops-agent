// Application frame: dark green sidebar, top bar (title, system health, notifications, account) and the page area.
import { api, h, icon, initials, logoMark, logout, set, state } from "./lib.js";

const NAV = [
  ["dashboard", "Dashboard", "home"],
  ["conversations", "Conversations", "chat"],
  ["agents", "Agents", "layers"],
  ["knowledge", "Knowledge Base", "book"],
  ["automation", "Automation", "zap"],
  ["evaluations", "Evaluations", "bars"],
  ["analytics", "Analytics", "pie"],
  ["traces", "Agent Trace", "nodes"],
  ["settings", "Settings", "settings"],
];
const NOTIF_ICON = { refund: ["alert", "amber"], reply: ["pencil", "blue"], escalation: ["users", "green"] };

// one listener closes any open dropdown when the user clicks elsewhere
document.addEventListener("click", (e) => {
  for (const menu of document.querySelectorAll(".menu:not([hidden])")) {
    if (!menu.parentElement.contains(e.target)) menu.hidden = true;
  }
});

function dropdown(trigger, menu) {
  trigger.addEventListener("click", () => {
    const willOpen = menu.hidden;
    document.querySelectorAll(".menu").forEach((m) => { m.hidden = true; });
    menu.hidden = !willOpen;
    trigger.setAttribute("aria-expanded", String(willOpen));
  });
  menu.addEventListener("click", (e) => { if (e.target.closest("a, button")) menu.hidden = true; });
  return h("div", { class: "has-menu" }, trigger, menu);
}

let applyStatus = () => {};

/** Re-read system health / approval counts (call after an action that changes them). */
export function refreshStatus() {
  return api("/api/status").then((status) => { state.status = status; applyStatus(status); }).catch(() => {});
}

/** Renders the frame and returns the element a page should fill. */
export function Shell({ page, title, subtitle, flush = false }) {
  const user = state.user;
  const badge = h("span", { class: "nav-badge", hidden: true });
  const health = h("a", { class: "health", href: "#/automation" }, h("i"), h("span", {}, "Checking"));
  const bellDot = h("i", { class: "bell-dot", hidden: true });
  const notifications = h("div", { class: "menu notif", hidden: true });
  const view = h("div", { class: flush ? "content flush" : "content" });

  const root = h("div", { class: "app" },
    h("aside", { class: "sidebar" },
      h("a", { class: "brand", href: "#/dashboard" }, logoMark(), "CADRAL AI"),
      h("nav", { class: "nav", "aria-label": "Main" }, NAV.map(([id, label, iconName]) =>
        h("a", { href: `#/${id}`, class: id === page ? "on" : "", "aria-current": id === page ? "page" : null },
          icon(iconName, 24), label, id === "conversations" ? badge : null))),
      h("div", { class: "sidebar-foot" },
        h("span", {}, "Workspace · Harbour & Lane"),
        h("a", { href: "#/chat", target: "_blank", rel: "noopener" }, icon("external", 15), "Open customer chat"))),
    h("div", { class: "main" },
      h("header", { class: "topbar" },
        h("button", { class: "iconbtn menu-btn", "aria-label": "Menu", onclick: () => root.classList.toggle("nav-open") }, icon("menu", 20)),
        h("div", { class: "topbar-title" }, h("h1", {}, title), subtitle ? h("p", {}, subtitle) : null),
        h("div", { class: "topbar-actions" },
          health,
          dropdown(h("button", { class: "iconbtn", "aria-label": "Notifications", "aria-haspopup": "true" }, icon("bell", 22), bellDot), notifications),
          dropdown(
            h("button", { class: "userbtn", "aria-label": "Account menu", "aria-haspopup": "true" },
              h("span", { class: "who" }, initials(user.name)), h("span", { class: "name" }, user.name), icon("chevronDown", 18)),
            h("div", { class: "menu", hidden: true },
              h("div", { class: "menu-head" }, h("b", {}, user.name), h("span", { class: "sub" }, `${user.email} · ${user.role}`)),
              h("a", { href: "#/settings" }, icon("settings", 18), "Settings"),
              h("a", { href: "#/chat", target: "_blank", rel: "noopener" }, icon("external", 18), "Open customer chat"),
              h("button", { class: "item", onclick: logout }, icon("logout", 18), "Sign out"))))),
      view));
  root.querySelector(".sidebar").addEventListener("click", (e) => { if (e.target.closest("a")) root.classList.remove("nav-open"); });

  applyStatus = (status) => {
    health.className = status.health.level === "healthy" ? "health" : `health ${status.health.level}`;
    health.title = status.health.details.join(" · ");
    set(health, h("i"), h("span", {}, status.health.label));
    bellDot.hidden = !status.approvals;
    badge.hidden = !status.pending_review;
    badge.textContent = status.pending_review;
    set(notifications,
      h("div", { class: "menu-head" }, h("b", {}, status.approvals ? `${status.approvals} item${status.approvals === 1 ? "" : "s"} waiting for a human` : "You're all caught up"),
        h("span", { class: "sub" }, "Refunds over the limit, replies held for review and open escalations")),
      status.notifications.map((n) => {
        const [iconName, tint] = NOTIF_ICON[n.type] || ["info", "grey"];
        return h("a", { href: n.link }, h("span", { class: `tile sm ${tint}` }, icon(iconName, 20)),
          h("span", { class: "grow" }, h("b", {}, n.title), h("span", { class: "sub" }, n.subtitle)));
      }),
      status.approvals ? h("a", { href: "#/approvals" }, icon("arrowRight", 18), "Open the approval queue") : null);
  };

  set(document.getElementById("app"), root);
  if (state.status) applyStatus(state.status);
  refreshStatus();
  return view;
}

export function loading(text = "Loading…") {
  return h("div", { class: "empty" }, h("p", {}, text));
}
