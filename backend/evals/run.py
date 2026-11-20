"""One command -> one score.

    cd backend && python -m evals.run            # full pipeline on a fresh, seeded database
    python -m evals.run --only orders --verbose  # filter + show failures
    python -m evals.run --min-score 0.85         # non-zero exit below threshold (CI gate)

Each case goes through the real pipeline (ingest -> queue -> router -> agent -> tools -> DB) against an
isolated SQLite database, then is graded on: routing, tool calls, answer content, citations,
"I don't know" behaviour, escalation and resulting database state.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

MAIN_DB_URL = os.environ.get("DATABASE_URL")
_tmp = Path(tempfile.mkdtemp(prefix="opsagent-eval-")) / "eval.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["REPLY_MODE"] = "auto"
for k in ("SLACK_WEBHOOK_URL", "SMTP_HOST", "IMAP_HOST", "TELEGRAM_BOT_TOKEN"):
    os.environ[k] = ""  # evals never touch real channels

import logging  # noqa: E402

logging.basicConfig(level=logging.ERROR)

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import jobs  # noqa: E402
from app.config import BASE_DIR, settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Base, Escalation, EvalRun, Message, Order, Refund, Trace  # noqa: E402
from app.orchestrator import ingest  # noqa: E402
from app.rag import kb  # noqa: E402
from app.seed import seed  # noqa: E402

HISTORY = HERE / "history.json"


def export_history(eng) -> None:
    """Write the latest recorded runs to evals/history.json. It is committed with the code, and a new database
    imports it on first boot (app/seed.py), so a fresh deployment shows the measured runs with their dates."""
    with Session(eng) as s:
        runs = s.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(6)).all()[::-1]
        HISTORY.write_text(json.dumps([{"created_at": r.created_at.isoformat(), "score": r.score,
                                        "routing_accuracy": r.routing_accuracy, "details": r.details} for r in runs], indent=1),
                           encoding="utf-8")


IDK_RE = re.compile(r"i (don't|do not) know|don't have (that )?information|not sure|unable to find|couldn't find (that|any)", re.I)


def grade(case: dict, reply: Message, trace: Trace, db: Session) -> dict[str, bool]:
    checks: dict[str, bool] = {"route": trace.route == case["route"]}
    # grade the wording, not the typography: curly quotes and dashes from a model count as their plain forms
    text = reply.content.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-"})
    tools_ok = [s["title"].split("(")[0] for s in trace.steps if s["type"] == "tool_call"]
    for i, alt in enumerate(case.get("tools", [])):
        checks[f"tool:{alt}"] = any(t in alt.split("|") for t in tools_ok)
    for i, pat in enumerate(case.get("contains", [])):
        checks[f"contains:{pat[:28]}"] = bool(re.search(pat, text, re.I))
    for pat in case.get("not_contains", []):
        checks[f"not_contains:{pat[:24]}"] = not re.search(pat, text, re.I)
    if case.get("doc"):
        cited = {c["doc"] for c in (reply.citations or [])}
        checks["cites_expected_doc"] = bool(cited & set(case["doc"].split("|")))
    if case["route"] == "support" and not case.get("idk"):
        checks["has_citation"] = bool(reply.citations) and bool(re.search(r"\[\d\]", text))
    if case.get("idk"):
        checks["says_idk"] = bool(IDK_RE.search(text))
        checks["no_citation_when_idk"] = not reply.citations
    if case.get("escalated"):
        checks["escalation_created"] = db.scalar(select(Escalation.id).where(Escalation.conversation_id == reply.conversation_id)) is not None
        checks["handoff_summary"] = any(s["type"] == "escalation" and s["data"].get("summary") for s in trace.steps)
    exp = case.get("db")
    if exp:
        o = db.get(Order, exp["order"])
        if "refunded_gbp" in exp:
            checks["db:refunded_gbp"] = abs(o.refunded_gbp - exp["refunded_gbp"]) < 0.01
        if "status" in exp:
            checks["db:status"] = o.status == exp["status"]
        if "address_contains" in exp:
            checks["db:address"] = exp["address_contains"] in o.shipping_address
        if "address_not_contains" in exp:
            checks["db:address_unchanged"] = exp["address_not_contains"] not in o.shipping_address
        if exp.get("pending_refund"):
            checks["db:refund_pending_approval"] = db.scalar(select(Refund.id).where(Refund.order_id == o.id, Refund.status == "pending_approval")) is not None
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="category filter: support|idk|orders|escalation|adversarial")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="comma-separated case ids, e.g. ord-07,esc-01")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--show", action="store_true", help="print every reply, not only failures")
    ap.add_argument("--no-record", action="store_true", help="do not save this run to the dashboard's eval history")
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--delay", type=float, default=float(os.environ.get("EVAL_DELAY", "0")), help="seconds between cases (free-tier rate limits)")
    ap.add_argument("--export-history", action="store_true", help="only rewrite evals/history.json from the recorded runs")
    args = ap.parse_args()
    if args.export_history:
        eng = create_engine((MAIN_DB_URL or f"sqlite:///{BASE_DIR / 'data' / 'app.db'}").replace("postgres://", "postgresql://", 1))
        export_history(eng)
        print(f"wrote {HISTORY}")
        return 0

    cases = [json.loads(l) for l in (HERE / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.only:
        cases = [c for c in cases if c["cat"] == args.only]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    cases = cases[: args.limit] if args.limit else cases

    seed()
    kb.build()
    mode = f"{settings.llm_provider}:{settings.llm_model}" if settings.llm_enabled else "offline-heuristics (no LLM key)"
    print(f"Running {len(cases)} eval cases | LLM: {mode} | retrieval: {kb.stats()['embedder']} + {kb.stats()['reranker']}\n")

    results, by_cat = [], defaultdict(list)
    for case in cases:
        db = SessionLocal()
        t0 = time.perf_counter()
        try:
            conv, msg = ingest(db, channel="web", text=case["text"], email=case.get("email"))
            for _ in range(8):  # retries are scheduled in the future; pull them forward
                jobs.drain()
                db.expire_all()
                reply = db.scalar(select(Message).where(Message.reply_to_id == msg.id))
                if reply:
                    break
                from sqlalchemy import update
                from app.models import Job, utcnow
                db.execute(update(Job).where(Job.status == "pending").values(next_run_at=utcnow()))
                db.commit()
            if not reply:
                raise RuntimeError("no reply produced")
            trace = db.get(Trace, reply.trace_id)
            checks = grade(case, reply, trace, db)
            score = sum(checks.values()) / len(checks)
            r = {"id": case["id"], "cat": case["cat"], "score": round(score, 3), "passed": score == 1.0, "checks": checks,
                 "route": trace.route, "reply": reply.content, "latency_ms": trace.latency_ms, "cost_usd": trace.cost_usd,
                 "tokens": trace.prompt_tokens + trace.completion_tokens}
        except Exception as e:  # a crash is a failed case, never a crashed run
            r = {"id": case["id"], "cat": case["cat"], "score": 0.0, "passed": False, "checks": {"ran": False}, "route": None,
                 "reply": f"ERROR: {e}", "latency_ms": int((time.perf_counter() - t0) * 1000), "cost_usd": 0, "tokens": 0}
        finally:
            db.close()
        results.append(r)
        by_cat[r["cat"]].append(r)
        mark = "PASS" if r["passed"] else ("PART" if r["score"] > 0 else "FAIL")
        print(f"  [{mark}] {r['id']:<8} {r['score']:.2f}  route={r['route']}")
        if args.show or (args.verbose and not r["passed"]):
            print(f"         failed: {[k for k, v in r['checks'].items() if not v]}\n         reply: {r['reply'][:320]}"
                  f"\n         {r['tokens']} tokens, {r['latency_ms']} ms")
        if args.delay:
            time.sleep(args.delay)

    n = len(results)
    score = sum(r["score"] for r in results) / n
    routing = sum(r["checks"].get("route", False) for r in results) / n
    lat = sorted(r["latency_ms"] for r in results)
    summary = {"score": round(score, 4), "pass_rate": round(sum(r["passed"] for r in results) / n, 4), "routing_accuracy": round(routing, 4),
               "cases": n, "llm": mode, "retrieval": kb.stats(),
               "by_category": {c: {"n": len(rs), "score": round(sum(r["score"] for r in rs) / len(rs), 4),
                                   "passed": sum(r["passed"] for r in rs)} for c, rs in by_cat.items()},
               "latency_ms": {"avg": int(sum(lat) / n), "p50": lat[n // 2], "p95": lat[min(n - 1, int(n * 0.95))]},
               "cost_usd": {"total": round(sum(r["cost_usd"] for r in results), 6), "per_message": round(sum(r["cost_usd"] for r in results) / n, 6)},
               "tokens": sum(r["tokens"] for r in results)}
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "latest.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    for c, v in summary["by_category"].items():
        print(f"  {c:<12} {v['passed']:>2}/{v['n']:<2} passed   score {v['score'] * 100:5.1f}%")
    print("-" * 64)
    print(f"  Routing accuracy : {routing * 100:.1f}%")
    print(f"  Latency          : avg {summary['latency_ms']['avg']} ms, p95 {summary['latency_ms']['p95']} ms")
    print(f"  Cost             : ${summary['cost_usd']['total']:.5f} total (${summary['cost_usd']['per_message']:.6f}/message), {summary['tokens']:,} tokens")
    print(f"\n  EVAL SCORE: {score * 100:.1f}%   ({summary['pass_rate'] * 100:.0f}% of cases fully passed)")
    print("=" * 64 + f"\n  details -> {out / 'latest.json'}")

    # Record the score in the main database so the dashboard can chart accuracy over time.
    # Filtered runs are debugging aids, not a score for the whole suite, so they are never recorded.
    if args.only or args.limit or args.ids or args.no_record:
        print("  (partial run - not recorded in the eval history)")
        return 0 if score >= args.min_score else 1
    try:
        url = MAIN_DB_URL or f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
        eng = create_engine(url.replace("postgres://", "postgresql://", 1))
        Base.metadata.create_all(eng)
        texts = {c["id"]: c["text"] for c in cases}
        compact = [{"id": r["id"], "cat": r["cat"], "text": texts[r["id"]], "score": r["score"], "passed": r["passed"],
                    "route": r["route"], "failed": [k for k, v in r["checks"].items() if not v],
                    "latency_ms": r["latency_ms"]} for r in results]  # per-case table on the Evaluations page
        with Session(eng) as s:
            s.add(EvalRun(score=score, routing_accuracy=routing, details=summary | {"results": compact}))
            s.commit()
        export_history(eng)
    except Exception as e:
        print(f"  (could not record run in main DB: {e})")
    return 0 if score >= args.min_score else 1


if __name__ == "__main__":
    raise SystemExit(main())
