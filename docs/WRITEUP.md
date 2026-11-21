# Write-up: Autonomous AI Operations Agent

## 1. Architecture

Every inbound message, whatever the channel, follows one path:

1. **Ingest** - the channel adapter (web chat API, IMAP poller / email webhook, Telegram webhook) calls
   `ingest()`, which commits the message and a `process_inbound` job in one transaction. Nothing external has
   been called yet, so nothing can be lost.
2. **Queue** - worker threads claim jobs with an optimistic lock. Failures retry with exponential backoff
   (5 s, 10 s, 20 s ... capped at 10 min); exhausted jobs go to a dead-letter state, raise a Slack alert and
   can be replayed from the dashboard. Jobs left `running` by a crash are requeued on boot.
3. **Router** - a cheap model (gpt-oss-20b on Groq, or the fine-tuned 1.5B classifier when `CLASSIFIER_URL` is set)
   picks `support`, `orders` or `escalation`. A deterministic risk scorer (legal threats, chargebacks, safety
   incidents, abuse, prompt-injection phrases) runs alongside it and **overrides** the model at high risk.
4. **Specialist agent**
   - *Support*: hybrid retrieval (BM25 + bge-small vectors, fused with Reciprocal Rank Fusion, top 12 reranked
     by a cross-encoder). If the best reranked score is below a threshold the agent answers "I don't know"
     without calling the LLM. Otherwise the LLM answers only from numbered sources; a post-check enforces that
     citations are present.
   - *Orders*: a tool-calling loop over six real functions. Each tool validates ownership and state, runs in a
     savepoint, and writes an audit-log row. Refunds over £150 are queued for an admin; duplicate refunds within
     15 minutes are deduplicated so a retried job can never pay twice. A reply that claims an action is checked
     against the tool calls of that turn, so the agent cannot tell a customer something happened when it did not.
   - *Escalation*: writes a structured handoff (summary, severity, reason, suggested action), opens an
     escalation, fires a Slack alert and sends an empathetic holding reply. The AI then stays out of that
     conversation until a human resolves it.
5. **Shared memory** - all agents receive the same context: transcript, rolling summary, customer tier, recent
   orders, agent-written notes, previous conversations and whether the customer was escalated before.
6. **Trace** - every step (route decision and why, retrieval hits with ranks, each tool call with arguments
   and result, guardrail triggers, injected examples) is stored with tokens, cost and latency, streamed live
   to the dashboard over SSE.

## 2. Trade-offs

| Decision | Why | Cost |
|---|---|---|
| DB-backed queue instead of Redis/Celery | one container, free tier, transactional with the message insert | polling latency (~1 s idle), not suited to >50 msg/s |
| SSE instead of WebSockets | one-way streaming is all the UI needs; works through any proxy | one connection per open conversation |
| In-memory BM25 + numpy vectors | 131 chunks - a vector DB would be overhead | rebuilds on boot; does not scale past ~100k chunks |
| Deterministic guardrails around the LLM | safety routing and refund limits must not depend on model mood | some false-positive escalations (cheap) |
| Fallbacks without an LLM | the system degrades instead of going down when the provider is rate-limited | weaker answers in fallback mode |
| No-build frontend (ES modules, hand-drawn SVG charts) | zero toolchain, tiny image, nothing to break in CI | less ergonomic than React for a larger UI |
| Two behaviour settings editable at runtime (reply mode, refund limit) | supervisors can tighten control without a redeploy; secrets stay in the environment | values are applied in process memory, so a second replica would need to reload them |
| Auto-send by default, review optional | the brief asks for autonomy; `REPLY_MODE=review` flips it | wrong answers can reach customers before review |

## 3. Fine-tuning

**Task:** intent classification for the Router (3 labels). **Model:** Qwen2.5-1.5B-Instruct + QLoRA (rank 16,
all attention and MLP projections, 3 epochs, loss only on the label token). **Data:** 412 train / 24 val /
57 test messages generated from templates with noise (typos, casing, greetings) plus hard negatives; test
templates never appear in training.

**Results** on the 57 held-out messages, measured on 21 November 2026 (raw files, including every misclassified
message, in [`finetune/results/`](../finetune/results)):

| Model | Accuracy | Macro-F1 | p50 latency | p95 latency | Cost / 1k requests |
|---|---|---|---|---|---|
| Qwen2.5-1.5B base (zero-shot) | 70.2% (40/57) | 0.705 | 73 ms | 118 ms | $0.0080 |
| Qwen2.5-1.5B + LoRA | **98.2%** (56/57) | 0.984 | 81 ms | 129 ms | $0.0081 |
| gpt-oss-120b on Groq (large API model) | 94.7% (54/57) | 0.952 | 433 ms | 534 ms | $0.0386 |

How it was measured: the two local models ran on a free Colab T4 in float16 with greedy decoding and were timed
in process, so their latency has no network in it. The API model was called from the development PC with the
application's own settings (reasoning effort low), so its 433 ms includes the internet round trip; Groq's own
queue and processing time was 359 ms at the median. Local cost is GPU time at $0.35 an hour with the GPU fully
used; API cost is tokens at list price (178 per request). No model gave an answer that could not be parsed.

What the numbers support:

- **Fine-tuning is what makes the small model usable.** Out of the box it sent 13 of the 19 order messages to the
  Support agent. Training 1.2% of its weights (18.5M of 1.56B parameters, 10 minutes on the T4) took it from 70%
  to 98%.
- **Against the large API model the accuracy gap is not established.** Two messages separate them, and on 57
  messages the 95% intervals overlap (91-100% against 86-98%). The API model's three misses are all refund
  requests for a faulty item, which this dataset labels `orders`; the fine-tuned model has learnt that
  convention, while the API model only had a one-line definition. The fine-tuned model's one miss is a general
  question with a typo ("what happens if my parcel is lat ein general?").
- **Latency is the clear difference:** 81 ms against 433 ms, and 35 ms when the label is read from a single
  forward pass, which is how `finetune/serve.py` answers (same 98.2%). A deployed classifier would add its own
  network hop, but no rate limit.
- **Cost only favours the small model at volume.** $0.008 against $0.039 per thousand requests assumes a busy
  GPU. A T4 left on all month costs about $252, which buys 6.5 million API calls, so below roughly that many
  routed messages a month the API is cheaper unless the GPU is shared or scales to zero.

Limits: the test set is small and synthetic, from the same generator as the training data (different
templates), so it measures unseen phrasings of the same kinds of request, not real customer traffic. It is one
training run with one seed. The adapter is not deployed: the live demo has no GPU and routes with gpt-oss-20b,
and the system eval in section 4 was not re-run with the fine-tuned router.

One practical finding: a T4 only emulates bfloat16. The first evaluation, in bfloat16, took 191 ms per request
for the base model; float16 gave the same 40/57 in 73 ms. Evaluation and the classifier server now pick the
half-precision type the GPU runs natively. Training still uses bfloat16 and took 10 minutes; float16 training
was not tried.

**When to use which**

- **Fine-tuned small model** - narrow, high-volume, stable tasks with a fixed output space: routing, sentiment,
  tone rewriting. Tens of milliseconds on a small GPU, data stays in-house, behaviour is consistent. The GPU is a
  fixed cost that only pays off at volume (see above), and the model cannot learn new facts without retraining.
- **RAG** - anything whose answer lives in documents that change: policies, prices, stock. Updating the
  knowledge base is a re-index, not a training run, and answers are citable and auditable.
- **Large API model** - open-ended reasoning, multi-step tool use, drafting nuanced replies, and the long tail
  the small model was never trained on. Highest quality, highest latency and cost, rate limits.

The system uses all three in that order of preference: small model to route, RAG to ground, large model only
where reasoning is needed. The feedback loop exports human-corrected replies as the next training set.

## 4. Eval scores

65 cases through the full pipeline (`python -m evals.run`, or the *Run evaluation* button on the dashboard):

| Mode | Overall | Routing | Support | IDK | Orders | Escalation | Adversarial |
|---|---|---|---|---|---|---|---|
| Offline fallbacks (no LLM) | **95.8%** | 100% | 89.8% | 100% | 100% | 100% | 100% |
| Groq free tier: gpt-oss-120b + gpt-oss-20b router, lite retrieval (2026-11-20) | **98.5%** | 98.5% | 97.2% | 100% | 98.6% | 100% | 100% |
| Same models + ONNX embeddings and cross-encoder | _not measured (no Docker or ONNX runtime on the build machine)_ | | | | | | |

The six partial failures offline are all retrieval/extraction misses of the heuristic reranker (for example
the Klarna instalment detail and the Oak Coffee Table price); with the LLM reading the retrieved passages they
pass. The live run took 2.2 s per message on average (p95 6.8 s, which includes free-tier rate-limit waits) and
cost $0.019 in total at list price: $0.0003 and about 1,700 tokens per message.

The two live misses are worth knowing. A general question ("how quickly do I have to report an item damaged in
transit?") was routed to the Orders agent, which answered correctly but without a citation because it has no
retrieval tool. And an address change on a dispatched order was refused correctly from shared memory without a
fresh `get_order_status` call. The fix for the first is to give the Orders agent a help-centre search tool.

Live testing before that run also caught a failure the offline suite cannot see: asked to refund a £799 order,
the model replied that the refund had been "logged" without calling the tool. The Orders agent now checks every
reply that claims a refund, cancellation or address change against that turn's tool calls; an unbacked claim is
sent back to the model once, and if it is still unbacked the customer gets a handoff instead of a promise.

## 5. What breaks at scale, and the fix

| Breaks | When | Fix |
|---|---|---|
| SQLite single writer; job polling | >~20 msg/s | Postgres (already supported), `SELECT ... FOR UPDATE SKIP LOCKED`, then Redis Streams / SQS |
| In-process SSE pub/sub | more than one API replica | Redis pub/sub or Postgres LISTEN/NOTIFY behind the same `events` interface |
| In-memory index | >100k chunks or frequent doc updates | pgvector / Qdrant with incremental upserts; keep BM25 in Postgres FTS or OpenSearch |
| Free-tier LLM rate limits | bursts | provider failover (already one fallback model), per-tenant token buckets, response caching for FAQs, the fine-tuned router removes one LLM call per message |
| Scheduler inside the web process | multiple replicas send duplicate reports | separate worker deployment with a leader lock, or platform cron hitting the job endpoint |
| Runtime settings and the eval runner live in one process | more than one replica | read settings from the database through a short cache; run evals as a queued job on a worker |
| Dashboard queries scan the period's rows | hundreds of thousands of traces | daily roll-up tables refreshed by a job, indexed on `created_at` |
| Prompt growth from learned rules/examples | hundreds of rules | retrieve top-k rules by similarity, periodically merge and prune, move stable ones into fine-tuning |
| Feedback quality | many reviewers | require a reason on reject, review conflicting rules, hold-out evals gate every prompt/rule change in CI |
| Refund abuse | adversarial customers | per-customer refund velocity limits, photo evidence step, lower auto-approval limit for new accounts |
| PII in logs and traces | compliance review | redact emails/addresses before LLM calls and trace storage; retention policy; UK/EU region hosting |

## 6. Known gaps

- Deployed on Railway's 30-day free trial (<https://cadral-ai-ops-agent-production.up.railway.app>, live since
  21 November 2026); it stops when the trial ends around mid-December unless it is moved. The other free routes
  did not work out: Render asked for a card before creating a free service, Koyeb requires one, and Hugging Face
  Docker Spaces need a paid plan.
- The live demo runs the embedding model and cross-encoder (`RAG_BACKEND=auto`), a path the eval suite has not
  been run on. What exists is a spot check of seven paraphrased questions on the live demo. Five of them are
  covered by the documents: lite retrieval answered two and half-answered a third (for example it said "I don't
  know" to "How long do I have to return an item?", though it answers "How many days ..."), the full models
  answered four. Both missed "Can I spread the cost over a few months?" (the Klarna instalments section), and both
  correctly declined the two questions the documents do not cover.
- The first run with those models took 7 to 9 seconds per reply: ONNX Runtime sized its thread pools from the
  host's core count while the container may use 2 vCPU. With the pools limited to the CPU quota
  (`RAG_THREADS`, or the cgroup limit) the same questions take about 2 seconds.
- The fine-tuned router is measured (section 3) but not deployed: the live demo has no GPU, so it routes with
  gpt-oss-20b. The adapter was trained in a temporary Colab session and is not stored anywhere; the notebook
  rebuilds it in about 13 minutes.
- The LLM-mode eval has been run once, with lite retrieval; the ONNX embedding + cross-encoder path is
  unmeasured.
- Groq's free tier allows about one full eval run a day on top of normal testing (8K tokens/min, 1,000
  requests/day per model), so sample traffic is loaded in offline mode rather than through the LLM.
- Slack, SMTP/IMAP and Telegram integrations are implemented but were not exercised against the real services.
- The demo video is not recorded.
- A fresh install has no history, so the dashboard charts start empty. `scripts/seed_demo.py` replays sample
  messages through the real pipeline with back-dated timestamps; the dashboard labels that data as sample traffic.
- The dashboard's flows (hold for review, approve, edit, refund approval, run evaluation) were driven end to
  end in a headless browser before the visual restyle of 21 November 2026. After the restyle every page, tab,
  menu and dialog was opened and checked by screenshot at 1729x677 (the main dashboard also at 1672x941,
  1366x611 and 390x844), but the flows were not submitted again. It has not been user-tested.
