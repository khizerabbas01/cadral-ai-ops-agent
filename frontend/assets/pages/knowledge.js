// Knowledge Base: the documents the Support agent may answer from, and a playground for the retrieval pipeline.
import { api, card, fmt, h, icon, pill, set, stat, state, table, toast } from "../lib.js";
import { Shell, loading } from "../shell.js";

const TYPE = { pdf: ["PDF", "red"], html: ["Web page", "blue"], md: ["Markdown", "violet"], csv: ["CSV", "amber"] };
const EXAMPLES = ["How much is next-day delivery?", "Can I return an opened pillow?", "Is there a recall on the toaster?", "What is the capital of France?"];

export async function Knowledge(ctx) {
  const view = Shell({ page: "knowledge", title: "Knowledge Base", subtitle: "Hybrid search over company documents, with citations and an honest \"I don't know\"" });
  set(view, loading());
  const data = await api("/api/kb/documents");
  if (ctx.stale()) return;
  const index = data.index || {}, byType = index.by_type || {};
  const results = h("div");
  const query = h("input", { type: "search", placeholder: "Ask something a customer would ask…", "aria-label": "Test query",
    onkeydown: (e) => { if (e.key === "Enter") search(); } });

  async function search(text) {
    if (text) query.value = text;
    if (!query.value.trim()) return;
    try {
      const r = await api(`/api/kb/search?q=${encodeURIComponent(query.value)}`);
      set(results,
        h("div", { class: r.confident ? "note" : "note amber", style: "margin:1rem 0" }, icon(r.confident ? "check" : "alert", 18),
          h("span", {}, r.confident
            ? `The Support agent would answer from these passages: top score ${r.top_score.toFixed(2)} is above the confidence threshold of ${r.threshold}.`
            : `Top score ${r.top_score.toFixed(2)} is below the confidence threshold of ${r.threshold}, so the Support agent would say "I don't know" instead of guessing.`)),
        table([["Rank", "num"], ["Rerank score", "num"], ["Keyword rank", "num"], ["Vector rank", "num"], "Passage"], r.hits.map((hit, i) => h("tr", {},
          h("td", { class: "num" }, i + 1), h("td", { class: "num" }, hit.score.toFixed(3)),
          h("td", { class: "num" }, hit.bm25_rank ?? "–"), h("td", { class: "num" }, hit.vector_rank ?? "–"),
          h("td", {}, h("div", { class: "strong" }, `${hit.title} — ${hit.section}`), h("div", { class: "muted small mono" }, hit.doc), h("div", { class: "small", style: "margin-top:.3rem" }, hit.text)))),
          { emptyText: "No passage matched." }));
    } catch (err) {
      toast(err.message, "error");
    }
  }
  async function reindex(e) {
    e.currentTarget.disabled = true;
    try {
      const r = await api("/api/admin/reindex", { method: "POST" });
      toast(`Re-indexed ${r.chunks} passages from ${r.documents} documents`);
    } catch (err) {
      toast(err.message, "error");
    }
    Knowledge(ctx);
  }

  set(view,
    h("div", { class: "grid g4" },
      h("section", { class: "card" }, h("div", { class: "card-body" }, stat("Documents", fmt.num(index.documents ?? data.documents.length), Object.entries(byType).map(([t, n]) => `${n} ${(TYPE[t === "markdown" ? "md" : t] || [t])[0]}`).join(" · ")))),
      h("section", { class: "card" }, h("div", { class: "card-body" }, stat("Indexed passages", fmt.num(index.chunks), "Each carries its document title and section"))),
      h("section", { class: "card" }, h("div", { class: "card-body" }, stat("Vector model", h("span", { style: "font-size:1rem" }, index.embedder || "indexing…"), "Fused with BM25 keyword search (RRF)"))),
      h("section", { class: "card" }, h("div", { class: "card-body" }, stat("Reranker", h("span", { style: "font-size:1rem" }, index.reranker || "indexing…"), `Answers need a score of ${data.threshold} or more`)))),
    card({ title: "Retrieval playground", iconName: "search",
      body: [h("div", { class: "cluster", style: "flex-wrap:nowrap" }, query, h("button", { class: "btn primary", onclick: () => search() }, icon("search", 16), "Search")),
        h("div", { class: "cluster", style: "gap:.4rem;margin-top:.7rem" }, h("span", { class: "muted small" }, "Try:"), EXAMPLES.map((q) => h("button", { class: "chip", onclick: () => search(q) }, q))),
        results] }),
    card({ title: "Documents", iconName: "file", flush: true,
      action: state.user.role === "admin" ? h("button", { class: "btn sm", onclick: reindex }, icon("refresh", 15), "Re-index") : null,
      body: table(["Document", "Type", ["Passages", "num"], ["Size", "num"]], data.documents.map((d) => {
        const [label, tint] = TYPE[d.type] || [d.type, "grey"];
        return h("tr", {}, h("td", {}, h("div", { class: "strong" }, d.title), h("div", { class: "muted small mono" }, d.name)), h("td", {}, pill(label, tint)),
          h("td", { class: "num" }, d.chunks), h("td", { class: "num nowrap" }, fmt.bytes(d.size)));
      })) }));
}
