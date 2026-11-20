# Autonomous AI Operations Agent

One AI system that runs customer operations for a UK e-commerce company (the fictional homeware retailer
**Harbour & Lane**): it answers customers, takes real actions on orders, hands risky cases to humans, learns
from corrections and reports to management.

| | |
|---|---|
| **Live demo** | _add URL after deploying (see [Deployment](#deployment))_ |
| **Demo video** | _add link_ |
| **Write-up** | [docs/WRITEUP.md](docs/WRITEUP.md) - architecture, trade-offs, fine-tuning, evals, scaling |

## What it does

| Requirement | Implementation |
|---|---|
| **Multi-agent** | `Router` -> `Support` (RAG) / `Orders` (function calling against the DB) / `Escalation` (human handoff + summary). All agents share one memory: conversation transcript + rolling summary + customer profile, orders, notes and past conversations. [`agents.py`](backend/app/agents.py), [`memory.py`](backend/app/memory.py) |
| **Advanced RAG** | 24 documents (8 PDF, 10 HTML pages, 5 Markdown FAQs, 1 product CSV). Hybrid BM25 + dense vectors, fused with RRF, reranked by a cross-encoder. Numbered citations on every answer; a confidence gate answers "I don't know" instead of guessing. [`rag.py`](backend/app/rag.py) |
| **Fine-tuning** | QLoRA fine-tune of Qwen2.5-1.5B as the router classifier, with a base-vs-fine-tuned-vs-API comparison script (accuracy, latency, cost). [`finetune/`](finetune) |
| **Self-improving loop** | Approve / edit / reject on the dashboard -> feedback rows -> (1) few-shot examples retrieved by similarity, (2) LLM-distilled prompt guidelines, (3) a re-training JSONL export. [`learning.py`](backend/app/learning.py) |
| **Automation** | Channels: web chat, email (IMAP poll or inbound webhook), Telegram. Daily management report by email (APScheduler). Slack alerts for escalations and system errors. Durable DB-backed job queue with backoff retries and a dead-letter state. [`jobs.py`](backend/app/jobs.py), [`channels.py`](backend/app/channels.py) |
| **Dashboard** | JWT login with `admin` / `agent` roles, live conversations with token streaming (SSE), agent trace (which agent, which tools, why), human approval queue with approve / edit / reject, analytics. [`frontend/`](frontend) |
| **Evals & monitoring** | 65 graded cases, one command (or one button), one score. Tokens, cost and latency recorded per LLM call and per request. [`evals/`](backend/evals) |
| **Deployment** | Single Docker image, all secrets from environment variables. [`Dockerfile`](Dockerfile), [`render.yaml`](render.yaml) |

## Architecture

```mermaid
flowchart LR
  subgraph Channels
    W[Web chat] --> ING
    E[Email IMAP / webhook] --> ING
    T[Telegram] --> ING
  end
  ING[ingest: persist message] --> Q[(Job queue<br/>retries + dead-letter)]
  Q --> R{Router agent<br/>LLM or fine-tuned 1.5B<br/>+ risk guardrails}
  R -->|policy / product| S[Support agent]
  R -->|order action| O[Orders agent]
  R -->|angry / risky| X[Escalation agent]
  S --> RAG[Hybrid search<br/>BM25 + vectors -> RRF -> rerank<br/>confidence gate]
  RAG --> KB[(24 docs:<br/>PDF, HTML, MD, CSV)]
  O --> TOOLS[Tools: get_order_status, issue_refund,<br/>cancel_order, update_shipping_address, ...]
  TOOLS --> DB[(Orders DB + audit log)]
  X --> SL[Slack alert] & H[Human queue with summary]
  MEM[(Shared memory:<br/>transcript, summary,<br/>customer history)] <--> S & O & X
  S & O & X --> REP[Reply + trace + cost] --> OUT[Deliver via channel]
  REP --> DASH[Dashboard: live stream, trace,<br/>approval queue, analytics]
  DASH -->|feedback| LEARN[Learning loop:<br/>examples, prompt rules, dataset]
  LEARN --> S & O
  LEARN -.-> FT[Fine-tune dataset]
  CRON[Daily scheduler] --> RPT[Management report email]
```

**Stack:** Python 3.12, FastAPI, SQLAlchemy (SQLite locally, Postgres via `DATABASE_URL`), Groq free tier
(Llama 3.3 70B for answers, Llama 3.1 8B for routing) through an OpenAI-compatible client (Gemini / OpenRouter
also work), fastembed (bge-small + MiniLM cross-encoder, ONNX on CPU), APScheduler. The frontend is plain ES
modules with hand-drawn SVG charts, served by the API - no Node build step.

## The dashboard

| Page | What you see |
|---|---|
| **Dashboard** | Total conversations, auto-resolution, response time and eval score with the change against the previous period; split by agent; live conversations; the human approval queue; conversation volume |
| **Conversations** | Inbox across channels, the thread with replies streaming in, citations, approve / edit / reject, and the agent trace for the selected reply |
| **Agents** | What each agent does and how it performed (routing methods, most-cited documents, tool calls, escalation severity), the escalation queue and the orders database with refunds and the audit log |
| **Knowledge Base** | The 24 documents and a retrieval playground: keyword rank, vector rank, rerank score and whether the agent would answer or say "I don't know" |
| **Automation** | Channel status, Slack alerts, the scheduled report with a preview, and the job queue with replay for failed jobs |
| **Evaluations** | Eval score by run and by category, every case with its failed checks, a *Run evaluation* button; the learning loop (feedback, guidelines, dataset export); the fine-tuning comparison |
| **Analytics** | Auto-resolution, accuracy, response time and cost per conversation over time; top issues; model usage |
| **Agent Trace** | Every handled message with route, tools, latency and outcome; select one to see each step and why it happened |
| **Settings** | Hold every reply for review (on/off) and the refund limit, changeable at runtime by an admin; models, retrieval and integration status |

Admins can do everything. Agents can review replies and handle escalations, but cannot approve refunds, change
settings, replay jobs or run evaluations.

## Quick start

```bash
git clone <repo> && cd cadral-ai-ops-agent
cp .env.example .env          # add LLM_API_KEY (free at console.groq.com), ADMIN_PASSWORD, AGENT_PASSWORD
docker compose up --build     # http://localhost:8000
```

Without Docker:

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cd backend && uvicorn app.main:app --port 8000
```

First boot seeds 12 customers, 40 orders, the product catalogue and the knowledge base.

- **Dashboard:** `http://localhost:8000` - sign in as `admin@demo.co.uk` or `agent@demo.co.uk` with the passwords
  you set in `.env` (if unset, random ones are generated and printed once in the server log).
- **Customer chat:** `http://localhost:8000/#/chat` - pick a demo customer and use the suggested prompts.
- **No API key?** Set `LLM_PROVIDER=mock`: routing, retrieval, tools and escalation still run on deterministic
  fallbacks, so the whole product is demoable offline. The top bar then shows *Offline Mode*.

### Sample traffic (optional)

A new install has no history, so the charts are empty. To give a demo something to show:

```bash
cd backend && python -m scripts.seed_demo         # ~150 conversations over 14 days; add --delay 2 on a free LLM tier
python -m evals.run                               # records an evaluation score for the dashboard
```

Every sample message is replayed through the **real** pipeline (routing, retrieval, tool calls, cost and
latency are genuine); only the timestamps are spread over the past days. No human reviews are invented and
nothing is emailed or posted. While this data is present the dashboard shows "Includes replayed sample traffic".

### Demo script

1. Customer chat as *James Patel*: "Where is my order UK-10002?" -> Orders agent calls `get_order_status`.
2. As *Emma Clarke*: "My order UK-10001 arrived damaged, I'd like a refund" -> `issue_refund` writes to the DB (see *Agents -> Orders & refunds* and the audit log).
3. As *Harry Evans*: refund for UK-10006 (£799) -> appears in the *Human Approval Queue* (over the £150 limit); an admin approves or rejects it.
4. "How much is next-day delivery?" -> cited answer. "What is the capital of France?" -> "I don't know".
5. "I want a manager or I'm doing a chargeback!" -> Escalation agent, Slack alert, human summary.
6. *Conversations*: click a reply to see its trace; **Edit** it with a reason -> *Evaluations -> Learning loop* shows the new guideline; ask a similar question again and the trace shows the injected example.
7. *Settings*: turn on **Hold every reply for human review**, send another chat message -> the reply waits in the approval queue and the customer sees nothing until it is approved.
8. *Conversations -> Simulate email* (or send a real email / Telegram message once those are configured).
9. *Automation*: preview or send the daily report, inspect the job queue. *Evaluations*: press **Run evaluation**.

## Evals

```bash
cd backend && python -m evals.run            # add -v for failures, --only orders, --min-score 0.9 for CI
```

Runs 65 messages through the real pipeline on an isolated database and grades routing, tool calls, answer
content, citations, "I don't know" behaviour, escalation and the resulting DB state. The score is saved to
`evals/results/latest.json` and shown on the *Evaluations* page, where an admin can also start a run.

| Mode | Score | Routing | Notes |
|---|---|---|---|
| Offline fallbacks (no LLM, LSA vectors + heuristic reranker) | **95.8%** (59/65 fully passed) | 100% | measured; orders 18/18, escalation 12/12, idk 5/5, support 21/27 |
| Groq Llama 3.3 70B + bge-small + cross-encoder | _run with your key and record here_ | | not yet measured |

`python -m scripts.smoke_test` runs a 19-check API test against a running server with a fresh database (auth,
roles, both channels, refund in DB, escalation, feedback loop, analytics, report, dashboard, runtime settings).

## Fine-tuning

Open [`finetune/finetune_router_colab.ipynb`](finetune/finetune_router_colab.ipynb) on a free Colab T4
(about 10 minutes): builds the dataset (412 train / 24 val / 57 test, no template shared between train and
test), trains the LoRA adapter, then prints the comparison table (accuracy, macro-F1, p50/p95 latency,
cost per 1k requests) for base vs fine-tuned vs Llama 3.3 70B. Results land in `finetune/results/` and then
appear on the *Evaluations -> Fine-tuning* page. Serve the adapter with `finetune/serve.py` and set
`CLASSIFIER_URL` to make it the production router.

## Configuration

Everything is an environment variable - see [`.env.example`](.env.example). Highlights:

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_FAST_MODEL` | LLM (groq / gemini / openrouter / mock) |
| `REPLY_MODE` | `auto` sends replies immediately; `review` holds every reply for human approval (default; an admin can switch it in *Settings*) |
| `REFUND_AUTO_LIMIT_GBP` | refunds above this are queued for an admin (default; also changeable in *Settings*) |
| `SLACK_WEBHOOK_URL` | escalation + error alerts |
| `SMTP_*`, `IMAP_HOST`, `REPORT_RECIPIENTS`, `REPORT_CRON_HOUR` | email channel and the daily report |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_POLLING` / `TELEGRAM_WEBHOOK_SECRET` | Telegram channel |
| `DATABASE_URL` | Postgres in production (Neon / Supabase free tier) |

Secrets are only ever read from the environment. The two behaviour settings an admin changes in the dashboard
are stored in the database and override the environment defaults.

## Deployment

The image is self-contained (API + frontend + knowledge base + ONNX models).

- **Hugging Face Spaces (Docker, free 16 GB RAM - recommended):** create a Docker Space, push this repo, set the
  secrets in *Settings -> Variables and secrets*, and set `PORT=7860`.
- **Render (free):** *New -> Blueprint* and point it at this repo ([`render.yaml`](render.yaml)). The free plan has
  512 MB RAM, so the blueprint sets `RAG_BACKEND=lite`; use a free Neon Postgres for `DATABASE_URL`.
- **Telegram webhook:** `https://api.telegram.org/bot<TOKEN>/setWebhook?url=<PUBLIC_BASE_URL>/api/channels/telegram/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>`

The dashboard loads the Inter typeface from Google Fonts and falls back to system fonts when offline.

## Repository layout

```
backend/app/        agents, orchestrator, rag, tools, memory, learning, jobs, channels, reporting,
                    main.py (API + write paths), dashboard_api.py (read models for the UI)
backend/scripts/    knowledge-base generator, sample-traffic loader, API smoke test
backend/evals/      dataset.jsonl (65 cases) + runner
backend/data/docs/  the 24 source documents
frontend/           index.html + assets/ (lib, charts, shell, one module per page) - no build step
finetune/           dataset builder, QLoRA training, evaluation, Colab notebook, classifier server
docs/WRITEUP.md     architecture, trade-offs, results, scaling
```
