// Renders one agent trace: which agent handled the message, which tools ran and why, with cost and latency.
import { agentPill, fmt, h, pill } from "../lib.js";

const STEP_LABEL = { route: "Routing", retrieval: "Retrieval", tool_call: "Tool call", llm: "LLM", guardrail: "Guardrail",
  escalation: "Handoff", learning: "Learning", error: "Error" };

export function stepList(steps) {
  if (!steps?.length) return h("p", { class: "muted" }, "No steps recorded yet.");
  return h("div", { class: "steps" }, steps.map((step) => h("div", { class: `step ${step.type}` },
    h("div", { class: "cluster", style: "gap:.45rem;flex-wrap:nowrap;align-items:flex-start" },
      h("div", { class: "step-title grow" }, step.title),
      step.ms != null ? h("span", { class: "muted small nowrap" }, fmt.ms(step.ms)) : null),
    h("div", { class: "cluster", style: "gap:.35rem;margin-top:.3rem" }, agentPill(step.agent), pill(STEP_LABEL[step.type] || step.type, "grey")),
    step.why ? h("div", { class: "step-why" }, h("b", {}, "Why: "), step.why) : null,
    step.data && Object.keys(step.data).length
      ? h("details", {}, h("summary", {}, "Show details"), h("pre", {}, JSON.stringify(step.data, null, 2))) : null)));
}

export function traceSummary(trace) {
  return [
    h("div", { class: "mini" },
      h("div", {}, h("b", {}, fmt.ms(trace.latency_ms)), h("span", {}, "Latency")),
      h("div", {}, h("b", {}, fmt.num(trace.prompt_tokens + trace.completion_tokens)), h("span", {}, "Tokens")),
      h("div", {}, h("b", {}, fmt.usd(trace.cost_usd, 5)), h("span", {}, "Cost"))),
    h("div", { class: "cluster", style: "gap:.4rem" }, h("span", { class: "muted small" }, "Handled by"), agentPill(trace.route),
      trace.route_confidence ? pill(`${Math.round(trace.route_confidence * 100)}% confidence`, "grey") : null,
      trace.resolved_by_ai ? pill("Auto-resolved", "green") : pill("Needs a human", "amber")),
    trace.error ? h("div", { class: "note red" }, h("span", {}, trace.error)) : null,
  ];
}
