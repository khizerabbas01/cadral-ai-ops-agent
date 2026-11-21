// Evaluations: the automated eval suite, the self-improving loop (human feedback) and the fine-tuning comparison.
import { agentPill, api, card, empty, fmt, h, icon, modal, onLeave, pill, set, stat, state, table, tabs, toast } from "../lib.js";
import { lineChart, meter, rankedBars } from "../charts.js";
import { Shell, loading } from "../shell.js";

const TABS = [["runs", "Eval runs"], ["learning", "Learning loop"], ["finetune", "Fine-tuning"]];
const CATEGORY = { support: "Support answers (RAG)", idk: "\"I don't know\" cases", orders: "Orders (tool calls)", escalation: "Escalation", adversarial: "Adversarial" };
const statCard = (...args) => h("section", { class: "card" }, h("div", { class: "card-body" }, stat(...args)));

async function Runs(view, ctx) {
  let onlyFailures = false, timer = null, last = null;
  onLeave(() => clearTimeout(timer));

  async function start(e) {
    e.currentTarget.disabled = true;
    try {
      await api("/api/evals/run", { method: "POST" });
      toast("Evaluation started");
    } catch (err) {
      toast(err.message, "error");
    }
    load();
  }
  function draw(d) {
    last = d;
    const L = d.latest, failed = L ? L.results.filter((r) => !r.passed) : [];
    const rows = L ? (onlyFailures ? failed : L.results) : [];
    set(view,
      h("div", { class: "page-head" },
        h("div", {}, h("h2", {}, "Automated evaluation"), h("p", {}, `${d.dataset_cases} sample messages with expected outcomes, run through the real pipeline on an isolated database.`)),
        h("div", { class: "right" }, d.running ? pill("Running…", "blue") : null,
          d.can_run ? h("button", { class: "btn primary", disabled: d.running, onclick: start }, icon("play", 15), "Run evaluation") : null)),
      d.run_disabled && state.user.role === "admin" ? h("div", { class: "note" }, icon("info", 18), h("span", {}, d.run_disabled)) : null,
      d.running || (d.exit_code != null && d.exit_code !== 0)
        ? card({ title: d.running ? "Run in progress" : "The last run did not finish", iconName: "cpu", body: h("pre", {}, d.output.join("\n") || "Starting…") }) : null,
      !L ? card({ body: empty("No evaluation has been recorded yet. Run it here, or from the command line: cd backend && python -m evals.run", "bars") }) : [
        h("div", { class: "grid g4" },
          statCard("Latest score", `${(L.score * 100).toFixed(1)}%`, `${fmt.dateTime(L.created_at)} · ${L.llm || ""}`),
          statCard("Routing accuracy", `${(L.routing_accuracy * 100).toFixed(1)}%`, "Right specialist chosen"),
          statCard("Cases fully passed", `${Math.round(L.pass_rate * L.cases)} / ${L.cases}`, "Every check on the case passed"),
          statCard("Per message", fmt.ms(L.latency_ms?.avg), `${fmt.usd(L.cost_usd?.per_message, 5)} · ${fmt.num(L.tokens)} tokens in total`)),
        h("div", { class: "grid g2" },
          card({ title: "Score by run", iconName: "bars", body: lineChart(d.runs.map((r) => ({ label: fmt.day(r.created_at), title: fmt.dateTime(r.created_at), value: r.score })),
            { max: 1, format: (v) => fmt.pct(v), caption: "Evaluation score by run", unit: "score", heightRem: 12 }) }),
          card({ title: "By category", iconName: "layers", flush: true, body: table(["Category", ["Passed", "num"], "Score"], Object.entries(L.by_category || {}).map(([key, c]) => h("tr", {},
            h("td", {}, CATEGORY[key] || key), h("td", { class: "num nowrap" }, `${c.passed} / ${c.n}`),
            h("td", { style: "min-width:11rem" }, h("div", { class: "cluster", style: "flex-wrap:nowrap" }, h("div", { class: "grow" }, meter(c.score, { label: key })), h("b", { class: "nowrap" }, fmt.pct(c.score, 1))))))) })),
        card({ title: "Results by case", iconName: "file", flush: true,
          action: h("button", { class: onlyFailures ? "chip on" : "chip", onclick: () => { onlyFailures = !onlyFailures; draw(last); } }, `Only failures (${failed.length})`),
          body: table(["Case", "Message", "Routed to", "Score", "Failed checks"], rows.map((r) => h("tr", {},
            h("td", { class: "mono nowrap" }, r.id), h("td", { style: "max-width:30rem" }, r.text), h("td", {}, agentPill(r.route) || "–"),
            h("td", {}, pill(fmt.pct(r.score), r.passed ? "green" : r.score > 0 ? "amber" : "red")),
            h("td", { class: "mono small" }, r.failed.length ? r.failed.join(", ") : "–"))), { emptyText: onlyFailures ? "No failing cases." : "No results stored for this run." }) }),
        h("div", { class: "note" }, icon("info", 18), h("span", {}, "The same suite runs from one command: ", h("span", { class: "mono" }, "cd backend && python -m evals.run"),
          ". Each case is graded on routing, tool calls, answer content, citations, \"I don't know\" behaviour, escalation and the resulting database state.")),
      ]);
  }
  async function load() {
    const d = await api("/api/evals");
    if (ctx.stale()) return;
    draw(d);
    if (d.running) timer = setTimeout(load, 2500);
  }
  await load();
}

async function Learning(view, ctx) {
  const d = await api("/api/learning");
  if (ctx.stale()) return;
  const admin = state.user.role === "admin";
  const reload = () => Learning(view, ctx);

  function addRule() {
    const text = h("textarea", { rows: 3, placeholder: "e.g. Always mention the 8pm cut-off when quoting next-day delivery" });
    const agent = h("select", { value: "all" }, [["all", "All agents"], ["support", "Support"], ["orders", "Orders"], ["escalation", "Escalation"]].map(([v, l]) => h("option", { value: v }, l)));
    modal({ title: "Add a guideline", body: [h("label", { class: "field" }, "Guideline", text), h("label", { class: "field" }, "Applies to", agent)],
      actions: [{ label: "Cancel", kind: "ghost" }, { label: "Add guideline", kind: "primary", onclick: async () => {
        if (!text.value.trim()) return false;
        await api("/api/rules", { method: "POST", body: { rule: text.value.trim(), agent: agent.value } });
        toast("Guideline added to the agent prompts");
        reload();
      } }] });
  }
  async function download() {
    const text = await api("/api/learning/dataset.jsonl");
    const link = h("a", { href: URL.createObjectURL(new Blob([text], { type: "application/jsonl" })), download: "retraining-dataset.jsonl" });
    link.click();
    URL.revokeObjectURL(link.href);
  }
  const toggle = (rule) => h("label", { class: "switch", title: rule.active ? "Active" : "Paused" },
    h("input", { type: "checkbox", checked: rule.active, disabled: !admin, "aria-label": "Guideline active", onchange: async (e) => {
      try {
        await api(`/api/rules/${rule.id}`, { method: "PATCH", body: { active: e.target.checked } });
        toast(e.target.checked ? "Guideline activated" : "Guideline paused");
      } catch (err) {
        toast(err.message, "error");
      }
    } }), h("i"));

  set(view,
    h("div", { class: "page-head" }, h("div", {}, h("h2", {}, "Self-improving loop"),
      h("p", {}, "Approve, edit or reject a reply in Conversations. Each correction becomes an example, a prompt guideline and a re-training record."))),
    h("div", { class: "grid g3" },
      statCard("Feedback captured", fmt.num(d.feedback.length), "Approvals, edits and rejections"),
      statCard("Active guidelines", fmt.num(d.rules.filter((r) => r.active).length), "Injected into the agents' prompts"),
      statCard("Re-training examples", fmt.num(d.dataset_size), admin ? h("button", { class: "btn sm", style: "margin-top:.4rem", onclick: download }, icon("download", 14), "Download JSONL") : "Chat-format records for the next fine-tune")),
    card({ title: "Learned guidelines", iconName: "brain", flush: true,
      action: admin ? h("button", { class: "btn sm", onclick: addRule }, icon("plus", 15), "Add guideline") : null,
      body: table(["Agent", "Guideline", "Source", "Active"], d.rules.map((r) => h("tr", {},
        h("td", {}, agentPill(r.agent)), h("td", {}, r.rule),
        h("td", { class: "muted small nowrap" }, r.source_feedback_id ? `Feedback #${r.source_feedback_id}` : "Added manually"), h("td", {}, toggle(r)))), { emptyText: "No guidelines yet." }) }),
    card({ title: "Feedback log", iconName: "pencil", flush: true,
      body: table(["Action", "Customer said", "AI reply", "Human version or reason"], d.feedback.map((f) => h("tr", {},
        h("td", { class: "nowrap" }, h("div", { class: "cluster", style: "gap:.3rem" }, pill(f.action[0].toUpperCase() + f.action.slice(1), f.action === "approve" ? "green" : f.action === "edit" ? "amber" : "red"), agentPill(f.agent))),
        h("td", { style: "max-width:18rem" }, f.customer_message), h("td", { class: "muted", style: "max-width:22rem" }, f.original_reply),
        h("td", { style: "max-width:22rem" }, f.action === "edit" ? f.final_reply : null, f.reason ? h("div", { class: "muted small" }, h("b", {}, "Reason: "), f.reason) : null))),
        { emptyText: "Approve, edit or reject a reply in Conversations to start teaching the system." }) }));
}

const FT_SHORT = { base: "Base model", finetuned: "Fine-tuned", api: "API model" };

/** How the comparison was produced: the training record, the served path and what the latency and cost figures include. */
function finetuneMethod(rows, t, accuracy) {
  const tuned = rows.find((r) => r.kind === "finetuned"), apiRow = rows.find((r) => r.kind === "api");
  const item = (label, text) => (text ? [h("dt", {}, label), h("dd", {}, text)] : null);
  const took = (s) => (s >= 90 ? `${Math.round(s / 60)} minutes` : `${Math.round(s)} seconds`);
  return h("dl", { class: "kv" },
    item("Training run", t && `${t.method}, rank ${t.rank}, ${t.epochs} epochs over ${fmt.num(t.train_examples)} messages. ${fmt.num(t.trainable_parameters)} of ${fmt.num(t.total_parameters)} parameters were trained (${fmt.pct(t.trainable_parameters / t.total_parameters, 1)}), in ${took(t.train_runtime_s)} on a ${t.hardware}.`),
    item("Validation loss", t?.val_loss_by_epoch?.length && `${t.val_loss_by_epoch.join(" → ")} after each epoch (${t.val_examples} messages from templates not used in training).`),
    item("As served", tuned?.single_pass && `The classifier server reads the label from one forward pass instead of generating text: ${accuracy(tuned.single_pass.accuracy)} accuracy at ${tuned.single_pass.latency_ms_p50} ms (p50) on the same GPU.`),
    item("API model timing", apiRow && `Measured end to end over the internet${apiRow.server_ms_p50 ? `; the provider's own queue and processing time was ${apiRow.server_ms_p50} ms (p50)` : ""}. About ${apiRow.tokens_per_request} tokens per request.`),
    item("Cost basis", rows.length && "Local models: GPU time at $0.35 an hour (an on-demand T4), one request at a time, so it is the price at full load; an idle GPU still costs the whole hour. API model: tokens at list price."));
}

/** Every held-out message the fine-tuned or the API model got wrong, with what each model answered. */
function finetuneErrors(rows) {
  const wrong = (r) => new Map((r.errors || []).map((e) => [e.text, e]));
  const maps = rows.map((r) => [r, wrong(r)]);
  const texts = [...new Set(rows.filter((r) => r.kind !== "base").flatMap((r) => (r.errors || []).map((e) => e.text)))];
  if (!texts.length) return null;
  const gold = (text) => maps.map(([, m]) => m.get(text)).find(Boolean).gold;
  return card({ title: "Messages the fine-tuned or API model got wrong", iconName: "alert", flush: true,
    body: table(["Customer message", "Correct", ...rows.map((r) => FT_SHORT[r.kind] || r.model)], texts.map((text) => h("tr", {},
      h("td", { style: "max-width:34rem" }, text), h("td", {}, agentPill(gold(text))),
      maps.map(([, m]) => h("td", {}, m.has(text) ? pill(m.get(text).predicted.replace(/^./, (c) => c.toUpperCase()), "red") : pill("Correct", "green")))))) });
}

async function Finetune(view, ctx) {
  const d = (await api("/api/evals")).finetune;
  if (ctx.stale()) return;
  const rows = d.results?.rows || [];
  const use = (iconName, title, text) => h("section", { class: "card" }, h("div", { class: "card-body" },
    h("div", { class: "agent-head" }, h("div", { class: "tile sm" }, icon(iconName, 22)), h("h3", {}, title)), h("p", { class: "muted", style: "margin-top:.7rem" }, text)));
  // The file stores accuracy rounded to four places; go back to the count so 56 of 57 reads 98.2%, not 98.3%
  const size = d.results?.test_size || 1;
  const exact = (v) => Math.round(v * size) / size;
  const accuracy = (v) => fmt.pct(exact(v), 1);
  const bars = (title, pick, format, opts = {}) => [h("div", { class: "subhead", style: opts.first ? "margin-top:0" : null }, title),
    rankedBars(rows.map((r) => [FT_SHORT[r.kind] || r.model, pick(r)]), { format, scaleToLargest: opts.scaleToLargest })];

  set(view,
    h("div", { class: "page-head" }, h("div", {}, h("h2", {}, "Fine-tuned router"),
      h("p", {}, "A 1.5B open model (Qwen2.5 + QLoRA) trained to classify messages as support, orders or escalation, compared with the base model and a large API model."))),
    rows.length
      ? [card({ title: `Comparison on ${d.results.test_size} held-out messages`, iconName: "bars", flush: true,
          body: table(["Model", ["Accuracy", "num"], ["Macro-F1", "num"], ["Invalid outputs", "num"], ["p50 latency", "num"], ["p95 latency", "num"], ["Cost per 1k requests", "num"], "Measured on"], rows.map((r) => h("tr", {},
            h("td", { class: "strong" }, r.model), h("td", { class: "num" }, accuracy(r.accuracy)), h("td", { class: "num" }, r.macro_f1.toFixed(3)), h("td", { class: "num" }, r.invalid_outputs),
            h("td", { class: "num nowrap" }, `${r.latency_ms_p50} ms`), h("td", { class: "num nowrap" }, `${r.latency_ms_p95} ms`), h("td", { class: "num" }, fmt.usd(r.cost_usd_per_1k_requests, 4)),
            h("td", { class: "muted nowrap" }, r.hardware || "–")))) }),
          h("div", { class: "grid g2" },
            card({ title: "Side by side", iconName: "target", body: [
              bars("Accuracy", (r) => exact(r.accuracy), (v) => fmt.pct(v, 1), { first: true }),
              bars("Median latency (lower is better)", (r) => r.latency_ms_p50, (v) => `${v} ms`),
              bars("Cost per 1k requests (lower is better)", (r) => r.cost_usd_per_1k_requests, (v) => fmt.usd(v, 4), { scaleToLargest: true })] }),
            card({ title: "How it was measured", iconName: "cpu", body: finetuneMethod(rows, d.training, accuracy) })),
          finetuneErrors(rows)]
      : card({ title: "Comparison not run yet", iconName: "cpu", body: h("div", { class: "stack tight" },
          h("p", {}, "Training needs a GPU, so the numbers are produced on a free Colab T4 and then committed to the repository. Nothing is shown here until that run exists."),
          h("ol", { style: "margin:0;padding-left:1.3rem;display:grid;gap:.4rem" },
            h("li", {}, "Open ", h("span", { class: "mono" }, "finetune/finetune_router_colab.ipynb"), " in Colab with a T4 runtime (about 13 minutes)."),
            h("li", {}, "It builds the dataset, trains the LoRA adapter and evaluates base, fine-tuned and API models on the held-out test set."),
            h("li", {}, "Copy ", h("span", { class: "mono" }, "finetune/results/"), " back into the repository - this page then shows accuracy, latency and cost.")),
          d.dataset ? h("p", { class: "muted" }, `Dataset ready: ${d.dataset.train} training, ${d.dataset.val} validation and ${d.dataset.test} test messages (no template shared between training and test).`) : null) }),
    h("div", { class: "grid g3" },
      use("cpu", "Fine-tuned small model", "Narrow, high-volume, stable tasks with a fixed set of answers: routing, sentiment, tone. Tens of milliseconds on a small GPU and data stays in-house, but the GPU is a fixed cost that only pays off at volume. Cannot learn new facts without retraining."),
      use("book", "RAG", "Anything whose answer lives in documents that change: policies, prices, stock. Updating knowledge is a re-index, not a training run, and every answer can cite its source."),
      use("sparkle", "Large API model", "Open-ended reasoning, multi-step tool use and the long tail a small model never saw. Highest quality, but the highest latency and cost, with rate limits.")));
}

export async function Evaluations(ctx) {
  const tab = TABS.some(([id]) => id === ctx.arg) ? ctx.arg : "runs";
  const view = Shell({ page: "evaluations", title: "Evaluations", subtitle: "Measured quality: an automated test set, human feedback and the fine-tuned router" });
  const body = h("div", { class: "stack" }, loading());
  set(view, tabs(TABS, tab, (id) => { location.hash = `#/evaluations/${id}`; }), body);
  await { runs: Runs, learning: Learning, finetune: Finetune }[tab](body, ctx);
}
