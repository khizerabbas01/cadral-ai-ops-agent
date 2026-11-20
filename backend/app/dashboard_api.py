"""Read models behind the dashboard UI: home KPIs, approval queue, agent stats, traces, evals,
automation status and runtime settings. main.py owns the write paths; this module mostly reads."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import jobs, runtime_settings
from .auth import current_user, require_admin
from .config import BASE_DIR, settings
from .db import get_db
from .models import (Conversation, Escalation, EvalRun, Job, LLMCall, Message, PromptRule, Refund, Trace, User, utcnow)
from .orchestrator import serialize_trace
from .rag import kb
from .reporting import topic_of
from .tools import TOOLS

router = APIRouter(prefix="/api")

AGENT_LABELS = {"support": "Support Agent (RAG)", "orders": "Orders Agent", "escalation": "Escalation Agent"}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _pct_change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _clip(text: str | None, width: int) -> str:
    """One-line preview: whitespace collapsed, cut at a word boundary and marked with an ellipsis."""
    return textwrap.shorten(text or "", width=width, placeholder="…")


# ====================================================================== health + approvals
def system_status(db: Session) -> dict:
    dead = db.scalar(select(func.count()).select_from(Job).where(Job.status == "dead")) or 0
    # 429s are free-tier throttling that the client retries - they are not a health problem
    llm_errors = db.scalar(select(func.count()).select_from(LLMCall).where(
        LLMCall.ok.is_(False), LLMCall.error.not_like("429%"), LLMCall.created_at >= utcnow() - timedelta(hours=1))) or 0
    issues = []
    if not kb.ready:
        issues.append("Knowledge base is still indexing")
    if dead:
        issues.append(f"{dead} job(s) failed permanently - see Automation")
    if llm_errors >= 3:
        issues.append(f"{llm_errors} LLM errors in the last hour")
    if issues:
        return {"level": "degraded", "label": "Needs Attention", "details": issues}
    if not settings.llm_enabled:
        return {"level": "offline", "label": "Offline Mode",
                "details": ["No LLM key configured - agents are running on deterministic fallbacks"]}
    return {"level": "healthy", "label": "System Healthy", "details": ["All services operational"]}


def approval_items(db: Session, user: User) -> list[dict]:
    """Everything waiting on a human: refunds over the limit, replies held for review, open escalations."""
    items: list[dict] = []
    for r in db.scalars(select(Refund).where(Refund.status == "pending_approval").order_by(Refund.id.desc()).limit(100)):
        items.append({"type": "refund", "id": r.id, "title": "High-value refund request",
                      "subtitle": f"Order {r.order_id} · £{r.amount_gbp:,.2f} · {_clip(r.reason, 80)}",
                      "link": "#/agents/orders", "created_at": _iso(r.created_at), "can_decide": user.role == "admin"})
    for m in db.scalars(select(Message).where(Message.status == "pending_review").order_by(Message.id.desc()).limit(100)):
        items.append({"type": "reply", "id": m.id, "title": "AI reply awaiting review",
                      "subtitle": f"{(m.agent or 'AI').title()} agent · {_clip(m.content, 90)}",
                      "link": f"#/conversations/{m.conversation_id}", "created_at": _iso(m.created_at), "can_decide": True})
    for e in db.scalars(select(Escalation).where(Escalation.status == "open").order_by(Escalation.id.desc()).limit(100)):
        items.append({"type": "escalation", "id": e.id, "title": "Escalation to human", "severity": e.severity,
                      "subtitle": f"{e.severity.title()} severity · {_clip(e.reason, 90)}",
                      "link": f"#/conversations/{e.conversation_id}", "created_at": _iso(e.created_at), "can_decide": True})
    return sorted(items, key=lambda i: i["created_at"], reverse=True)


def approval_preview(items: list[dict], n: int = 3) -> list[dict]:
    """Newest item of each kind first (so the widget shows what *kinds* of work are waiting), then newest overall."""
    counts = Counter(i["type"] for i in items)
    picked, seen = [], set()
    for kind in ("refund", "reply", "escalation"):
        first = next((i for i in items if i["type"] == kind), None)
        if first:
            picked.append(first | {"more": counts[kind] - 1})
            seen.add((kind, first["id"]))
    for i in items:
        if len(picked) >= n:
            break
        if (i["type"], i["id"]) not in seen:
            picked.append(i | {"more": 0})
    return picked[:n]


@router.get("/status")
def status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Small payload for the top bar: health pill, notification bell and nav badges."""
    items = approval_items(db, user)
    return {"health": system_status(db), "approvals": len(items),
            "pending_review": sum(i["type"] == "reply" for i in items),
            "notifications": [{k: i[k] for k in ("type", "title", "subtitle", "link", "created_at")} for i in items[:6]]}


@router.get("/approvals")
def approvals(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return approval_items(db, user)


# ====================================================================== dashboard home
@router.get("/dashboard")
def dashboard(days: int = 7, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """KPIs for the last `days` calendar days (UTC) with the change against the `days` before them."""
    days = max(2, min(days, 90))
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)
    day_of = lambda created: min(days - 1, max(0, (created - start).days))

    conv_daily, conv_cur, conv_prev, by_agent = [0] * days, 0, 0, Counter()
    for created, agent in db.execute(select(Conversation.created_at, Conversation.last_agent)
                                     .where(Conversation.created_at >= prev_start)):
        if created >= start:
            conv_cur += 1
            conv_daily[day_of(created)] += 1
            by_agent[agent or "pending"] += 1
        else:
            conv_prev += 1

    n, ok, lat = [0] * days, [0] * days, [0] * days
    cur, prev = {"n": 0, "ok": 0, "lat": 0}, {"n": 0, "ok": 0, "lat": 0}
    for created, latency, resolved in db.execute(select(Trace.created_at, Trace.latency_ms, Trace.resolved_by_ai)
                                                 .where(Trace.created_at >= prev_start)):
        bucket = cur if created >= start else prev
        bucket["n"] += 1
        bucket["ok"] += int(bool(resolved))
        bucket["lat"] += latency or 0
        if created >= start:
            i = day_of(created)
            n[i] += 1
            ok[i] += int(bool(resolved))
            lat[i] += latency or 0
    rate = lambda b: b["ok"] / b["n"] if b["n"] else None
    avg = lambda b: b["lat"] / b["n"] if b["n"] else None
    res_cur, res_prev = rate(cur), rate(prev)

    evals = db.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(12)).all()[::-1]
    eval_delta = round(evals[-1].score - evals[-2].score, 4) if len(evals) >= 2 else None

    live = []
    for c in db.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(5)):
        last = db.scalar(select(Message).where(Message.conversation_id == c.id, Message.role == "customer")
                         .order_by(Message.id.desc()).limit(1))
        text = last.content if last else c.subject
        live.append({"id": c.id, "customer": c.customer.name if c.customer else "Guest", "channel": c.channel,
                     "topic": topic_of(text), "preview": text[:140],
                     "agent": c.last_agent, "status": c.status, "updated_at": _iso(c.updated_at)})

    segments = [{"key": k, "label": AGENT_LABELS[k], "count": by_agent.get(k, 0)} for k in AGENT_LABELS]
    if by_agent.get("pending"):
        segments.append({"key": "pending", "label": "Awaiting an agent", "count": by_agent["pending"]})
    items = approval_items(db, user)
    return {
        "days": days, "from": _iso(start), "to": _iso(now),
        "kpis": {
            "conversations": {"value": conv_cur, "delta": _pct_change(conv_cur, conv_prev), "unit": "%",
                              "higher_is_better": True, "spark": conv_daily},
            "auto_resolution": {"value": res_cur, "unit": "pp", "higher_is_better": True,
                                "delta": round((res_cur - res_prev) * 100, 1) if res_cur is not None and res_prev is not None else None,
                                "spark": [round(ok[i] / n[i], 4) if n[i] else None for i in range(days)]},
            "response_time_ms": {"value": avg(cur), "delta": _pct_change(avg(cur), avg(prev)), "unit": "%",
                                 "higher_is_better": False,
                                 "spark": [round(lat[i] / n[i], 1) if n[i] else None for i in range(days)]},
            "eval_score": {"value": evals[-1].score if evals else None, "delta": eval_delta, "unit": "abs",
                           "higher_is_better": True, "spark": [round(e.score, 4) for e in evals],
                           "at": _iso(evals[-1].created_at) if evals else None},
        },
        "agents": {"total": conv_cur, "segments": segments},
        "live": live,
        "approvals": {"total": len(items), "items": approval_preview(items)},
        "volume": [{"date": (start + timedelta(days=i)).strftime("%Y-%m-%d"), "conversations": conv_daily[i], "messages": n[i]}
                   for i in range(days)],
        "health": system_status(db),
        "demo_data": runtime_settings.get_flag(db, "demo_seeded_at"),
    }


# ====================================================================== agent trace explorer
@router.get("/traces")
def list_traces(limit: int = 60, route: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(Trace).order_by(Trace.id.desc()).limit(max(1, min(limit, 200)))
    if route:
        q = q.where(Trace.route == route)
    traces = db.scalars(q).all()
    if not traces:
        return []
    inbound = {m.id: m for m in db.scalars(select(Message).where(Message.id.in_([t.inbound_message_id for t in traces if t.inbound_message_id])))}
    replies = {m.trace_id: m for m in db.scalars(select(Message).where(Message.trace_id.in_([t.id for t in traces])))}
    convs = {c.id: c for c in db.scalars(select(Conversation).where(Conversation.id.in_({t.conversation_id for t in traces})))}
    out = []
    for t in traces:
        c, msg, reply = convs.get(t.conversation_id), inbound.get(t.inbound_message_id), replies.get(t.id)
        out.append(serialize_trace(t) | {
            "conversation_id": t.conversation_id, "channel": c.channel if c else None,
            "customer": c.customer.name if c and c.customer else "Guest",
            "message": msg.content if msg else "", "reply": reply.content if reply else "",
            "reply_status": reply.status if reply else None,
            "tools": [s["title"].split("(")[0] for s in (t.steps or []) if s.get("type") == "tool_call"]})
    return out


# ====================================================================== agents overview
@router.get("/agents")
def agents(days: int = 7, db: Session = Depends(get_db), user: User = Depends(current_user)):
    days = max(1, min(days, 90))
    since = utcnow() - timedelta(days=days)
    traces = db.scalars(select(Trace).where(Trace.created_at >= since)).all()
    stats = {k: {"handled": 0, "resolved": 0, "latency": 0, "cost": 0.0} for k in AGENT_LABELS}
    methods, tool_calls, tool_fail = Counter(), Counter(), Counter()
    overrides = idk = examples = 0
    conf_sum = 0.0
    for t in traces:
        s = stats.get(t.route)
        if s:
            s["handled"] += 1
            s["resolved"] += int(bool(t.resolved_by_ai))
            s["latency"] += t.latency_ms or 0
            s["cost"] += t.cost_usd or 0.0
        conf_sum += t.route_confidence or 0.0
        for st in t.steps or []:
            kind, data = st.get("type"), st.get("data") or {}
            if kind == "route":
                method = data.get("method") or "conversation state"
                methods[method.split("+")[0]] += 1
                overrides += int("guardrail" in method)
            elif kind == "tool_call":
                name = st.get("title", "").split("(")[0]
                tool_calls[name] += 1
                tool_fail[name] += int(not data.get("ok", True))
            elif kind == "guardrail" and ("don't know" in st.get("title", "") or "declined" in st.get("title", "")):
                idk += 1
            elif kind == "learning":
                examples += 1
    cited = Counter()
    for (cits,) in db.execute(select(Message.citations).where(Message.role == "assistant", Message.agent == "support",
                                                             Message.created_at >= since)):
        for c in cits or []:
            cited[c.get("title") or c.get("doc")] += 1
    severities = dict(db.execute(select(Escalation.severity, func.count()).where(Escalation.created_at >= since)
                                 .group_by(Escalation.severity)).all())
    open_esc = db.scalar(select(func.count()).select_from(Escalation).where(Escalation.status != "resolved")) or 0
    total = len(traces)

    def block(key: str) -> dict:
        s = stats[key]
        h = s["handled"]
        return {"handled": h, "share": round(h / total, 4) if total else None,
                "auto_resolved": round(s["resolved"] / h, 4) if h else None,
                "avg_latency_ms": round(s["latency"] / h) if h else None,
                "avg_cost_usd": round(s["cost"] / h, 6) if h else None}

    answer_model = settings.llm_model if settings.llm_enabled else "offline fallback (no LLM key)"
    router_model = ("fine-tuned Qwen2.5-1.5B" if settings.classifier_url else settings.llm_fast_model) if settings.llm_enabled else "heuristic rules (no LLM key)"
    return {
        "days": days, "total": total,
        "router": {"model": router_model, "handled": total, "avg_confidence": round(conf_sum / total, 3) if total else None,
                   "methods": methods.most_common(), "guardrail_overrides": overrides},
        "support": block("support") | {"model": answer_model, "idk": idk, "top_documents": cited.most_common(5),
                                       "kb": kb.stats() if kb.ready else None},
        "orders": block("orders") | {"model": answer_model, "refund_limit_gbp": settings.refund_auto_limit_gbp,
                                     "tools": [{"name": name, "description": desc, "calls": tool_calls.get(name, 0),
                                                "failures": tool_fail.get(name, 0)} for name, (_, desc, _) in TOOLS.items()]},
        "escalation": block("escalation") | {"model": answer_model, "by_severity": severities, "open": open_esc},
        "learning": {"examples_injected": examples,
                     "active_rules": db.scalar(select(func.count()).select_from(PromptRule).where(PromptRule.active.is_(True))) or 0},
    }


# ====================================================================== automation
@router.get("/automation")
def automation(db: Session = Depends(get_db), user: User = Depends(current_user)):
    since = utcnow() - timedelta(days=7)
    by_channel = dict(db.execute(select(Conversation.channel, func.count()).where(Conversation.created_at >= since)
                                 .group_by(Conversation.channel)).all())
    recipients = [r for r in settings.report_recipients.split(",") if r.strip()]
    return {
        "channels": [
            {"key": "web", "label": "Web chat", "inbound": True, "outbound": True, "conversations_7d": by_channel.get("web", 0),
             "detail": "Chat widget with streamed replies (server-sent events)"},
            {"key": "email", "label": "Email", "inbound": bool(settings.imap_host), "outbound": bool(settings.smtp_host),
             "conversations_7d": by_channel.get("email", 0),
             "detail": (f"IMAP inbox polled every {settings.imap_poll_seconds}s" if settings.imap_host
                        else "Inbound webhook available; set IMAP_HOST to poll a mailbox") + "; replies sent over SMTP"},
            {"key": "telegram", "label": "Telegram", "inbound": bool(settings.telegram_bot_token),
             "outbound": bool(settings.telegram_bot_token), "conversations_7d": by_channel.get("telegram", 0),
             "detail": "Bot " + ("long polling" if settings.telegram_polling else "webhook") + " via the Telegram Bot API"},
        ],
        "alerts": {"slack": bool(settings.slack_webhook_url),
                   "triggers": ["Every escalation, with severity and summary", "Jobs that fail permanently",
                                "LLM provider failures", "Unhandled API errors"]},
        "report": {"hour": settings.report_cron_hour, "timezone": "Europe/London", "recipients": len(recipients),
                   "smtp": bool(settings.smtp_host), "next_run": jobs.next_report_time()},
        "queue": {"counts": dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all()),
                  "policy": "Each external call runs in a persisted job. Failures retry after 5s, 10s, 20s ... (capped at 10 min); "
                            "exhausted jobs move to a dead-letter state, raise a Slack alert and can be replayed."},
    }


# ====================================================================== evaluations
_eval: dict = {"proc": None, "log": deque(maxlen=200), "started_at": None, "exit_code": None}
_eval_lock = threading.Lock()


def _eval_running() -> bool:
    proc = _eval["proc"]
    return proc is not None and proc.poll() is None


def _line_count(path: Path) -> int | None:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return None


def _finetune() -> dict:
    root = BASE_DIR.parent / "finetune"
    results = None
    try:
        results = json.loads((root / "results" / "results.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    sizes = {split: _line_count(root / "data" / f"{split}.jsonl") for split in ("train", "val", "test")}
    return {"results": results, "dataset": sizes if any(sizes.values()) else None}


def _run_summary(r: EvalRun) -> dict:
    d = r.details or {}
    return {"id": r.id, "score": r.score, "routing_accuracy": r.routing_accuracy, "pass_rate": d.get("pass_rate"),
            "cases": d.get("cases"), "llm": d.get("llm"), "created_at": _iso(r.created_at)}


@router.get("/evals")
def evals(db: Session = Depends(get_db), user: User = Depends(current_user)):
    runs = db.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(30)).all()
    latest = None
    if runs:
        d = runs[0].details or {}
        latest = _run_summary(runs[0]) | {k: d.get(k) for k in ("by_category", "latency_ms", "cost_usd", "tokens", "retrieval")} \
            | {"results": d.get("results") or []}
    return {"runs": [_run_summary(r) for r in reversed(runs)], "latest": latest,
            "dataset_cases": _line_count(BASE_DIR / "evals" / "dataset.jsonl"),
            "running": _eval_running(), "started_at": _eval["started_at"], "exit_code": _eval["exit_code"],
            "output": list(_eval["log"])[-30:], "finetune": _finetune(),
            "can_run": user.role == "admin" and settings.allow_eval_run,
            "run_disabled": None if settings.allow_eval_run else "Running the suite is switched off on this host (it needs a "
                            "second process). Run it from the command line; recorded runs are shown below."}


@router.post("/evals/run")
def run_evals(user: User = Depends(require_admin)):
    """Runs the same one-command suite as `python -m evals.run` in a child process (its own temporary database)."""
    if not settings.allow_eval_run:
        raise HTTPException(403, "Running evaluations from the dashboard is switched off on this host")
    with _eval_lock:
        if _eval_running():
            raise HTTPException(409, "An evaluation run is already in progress")
        proc = subprocess.Popen([sys.executable, "-m", "evals.run"], cwd=str(BASE_DIR), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                env=os.environ | {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        _eval["log"].clear()
        _eval.update(proc=proc, started_at=_iso(utcnow()), exit_code=None)

    def pump() -> None:
        for line in proc.stdout:
            _eval["log"].append(line.rstrip())
        _eval["exit_code"] = proc.wait()

    threading.Thread(target=pump, name="eval-run", daemon=True).start()
    return {"started": True}


# ====================================================================== settings
class SettingsIn(BaseModel):
    reply_mode: str | None = None
    refund_auto_limit_gbp: float | None = None


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), user: User = Depends(current_user)):
    out = {
        "can_edit": user.role == "admin",
        "behaviour": {"reply_mode": settings.reply_mode, "refund_auto_limit_gbp": settings.refund_auto_limit_gbp},
        "llm": {"provider": settings.llm_provider, "enabled": settings.llm_enabled, "model": settings.llm_model,
                "fast_model": settings.llm_fast_model, "fine_tuned_router": bool(settings.classifier_url)},
        "rag": (kb.stats() if kb.ready else {}) | {"backend": settings.rag_backend, "confidence_threshold": kb.threshold},
        "integrations": {"slack": bool(settings.slack_webhook_url), "smtp": bool(settings.smtp_host),
                         "imap": bool(settings.imap_host), "telegram": bool(settings.telegram_bot_token)},
        "workspace": {"name": "Harbour & Lane", "public_base_url": settings.public_base_url},
    }
    if user.role == "admin":
        out["users"] = [{"id": u.id, "name": u.name, "email": u.email, "role": u.role}
                        for u in db.scalars(select(User).order_by(User.id))]
    return out


@router.patch("/settings")
def patch_settings(body: SettingsIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    try:
        applied = runtime_settings.update(db, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"applied": applied}
