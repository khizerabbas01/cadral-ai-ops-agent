"""Analytics + the daily management report (email)."""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import Conversation, Escalation, EvalRun, Feedback, Message, Refund, Trace, utcnow

log = logging.getLogger("reporting")

# First match wins, so the most specific / most urgent topics come first.
TOPIC_RULES = [
    ("Complaints & escalations", r"\b(complain|unacceptable|disgust|terrible|worst|furious|useless|manager|supervisor|real person|human|solicitor|court|chargeback|dispute|trading standards|scam|fraud|unauthori[sz]ed)"),
    ("Damaged / faulty items", r"\b(damaged|broken|faulty|defect|crack|chipped|not working|stopped working|caught fire|electric shock|wrong item|missing item)"),
    ("Cancellations & changes", r"\b(cancel|change (my |the )?(delivery |shipping )?address|amend|add an item)"),
    ("Refunds & returns", r"\b(refund|return|money back|exchange)"),
    ("Delivery & tracking", r"\b(deliver|track|parcel|arriv|courier|dispatch|late|where is|shipping|ship to|collect|order uk|update on|sent yet|status of)"),
    ("Payments & discounts", r"\b(pay|klarna|card|discount|code|voucher|price match|gift card|points|student|nhs)"),
    ("Account & privacy", r"\b(password|account|log ?in|sign ?in|unsubscribe|privacy|my data)"),
    ("Product questions", r"\b(stock|price|sell|size|material|dimension|product|warranty|guarantee|care|wash|made|recall|recyclable|packaging|assembly|dishwasher|showroom|stores?)"),
]


def topic_of(text: str) -> str:
    t = text.lower()
    for name, pat in TOPIC_RULES:
        if re.search(pat, t):
            return name
    return "General enquiries"


def accuracy(db: Session, since: datetime) -> dict:
    rows = db.execute(select(Feedback.action, func.count()).where(Feedback.created_at >= since).group_by(Feedback.action)).all()
    c = dict(rows)
    reviewed = sum(c.values())
    approved = c.get("approve", 0)
    return {"reviewed": reviewed, "approved": approved, "edited": c.get("edit", 0), "rejected": c.get("reject", 0),
            "human_agreement": round(approved / reviewed, 3) if reviewed else None}


def compute_metrics(db: Session, since: datetime, until: datetime | None = None) -> dict:
    until = until or utcnow()
    traces = db.scalars(select(Trace).where(Trace.created_at >= since, Trace.created_at < until)).all()
    inbound = db.scalars(select(Message).where(Message.role == "customer", Message.created_at >= since,
                                               Message.created_at < until)).all()
    convs = db.scalars(select(Conversation).where(Conversation.created_at >= since, Conversation.created_at < until)).all()
    refunds = db.scalars(select(Refund).where(Refund.created_at >= since, Refund.created_at < until)).all()
    escalations = db.scalars(select(Escalation).where(Escalation.created_at >= since, Escalation.created_at < until)).all()
    n = len(traces)
    latencies = sorted(t.latency_ms for t in traces if t.latency_ms)
    p = lambda q: latencies[min(len(latencies) - 1, int(q * len(latencies)))] if latencies else 0
    cost = sum(t.cost_usd for t in traces)
    conv_ids = {t.conversation_id for t in traces}
    last_eval = db.scalar(select(EvalRun).order_by(EvalRun.id.desc()).limit(1))
    return {
        "period": {"from": since.isoformat(), "to": until.isoformat()},
        "messages_in": len(inbound),
        "conversations": len(convs),
        "by_channel": dict(Counter(c.channel for c in convs)),
        "by_agent": dict(Counter(t.route for t in traces)),
        "top_issues": Counter(topic_of(m.content) for m in inbound).most_common(5),
        "auto_resolution_rate": round(sum(t.resolved_by_ai for t in traces) / n, 3) if n else None,
        "escalations": len(escalations),
        "escalations_by_severity": dict(Counter(e.severity for e in escalations)),
        "refunds_issued": {"count": sum(r.status == "issued" for r in refunds),
                           "total_gbp": round(sum(r.amount_gbp for r in refunds if r.status == "issued"), 2),
                           "pending_approval": sum(r.status == "pending_approval" for r in refunds)},
        "latency_ms": {"avg": int(sum(latencies) / len(latencies)) if latencies else 0, "p50": p(0.5), "p95": p(0.95)},
        "cost_usd": {"total": round(cost, 5), "per_conversation": round(cost / len(conv_ids), 6) if conv_ids else 0},
        "tokens": sum(t.prompt_tokens + t.completion_tokens for t in traces),
        "errors": sum(1 for t in traces if t.error),
        "accuracy": accuracy(db, since),
        "latest_eval": {"score": last_eval.score, "at": last_eval.created_at.isoformat()} if last_eval else None,
    }


def render_report(m: dict, day: str) -> tuple[str, str]:
    acc = m["accuracy"]
    acc_txt = f"{acc['human_agreement'] * 100:.0f}% of {acc['reviewed']} reviewed replies approved unchanged" if acc["reviewed"] else "no human reviews yesterday"
    eval_txt = f"{m['latest_eval']['score'] * 100:.1f}%" if m["latest_eval"] else "n/a"
    auto = f"{m['auto_resolution_rate'] * 100:.0f}%" if m["auto_resolution_rate"] is not None else "n/a"
    issues = "".join(f"<li>{name} - {count}</li>" for name, count in m["top_issues"]) or "<li>No messages</li>"
    text = (f"Harbour & Lane - AI Operations daily report ({day})\n\n"
            f"Volume: {m['messages_in']} messages, {m['conversations']} new conversations {m['by_channel']}\n"
            f"Auto-resolved by AI: {auto}  |  Escalations: {m['escalations']}\n"
            f"Refunds issued: {m['refunds_issued']['count']} totalling £{m['refunds_issued']['total_gbp']:.2f} "
            f"({m['refunds_issued']['pending_approval']} awaiting approval)\n"
            f"Top issues: {', '.join(f'{a} ({b})' for a, b in m['top_issues'])}\n"
            f"AI accuracy: {acc_txt}; latest eval score {eval_txt}\n"
            f"Avg response time: {m['latency_ms']['avg']} ms (p95 {m['latency_ms']['p95']} ms)\n"
            f"LLM cost: ${m['cost_usd']['total']:.4f} (${m['cost_usd']['per_conversation']:.5f}/conversation)\n")
    card = lambda label, value: (f'<td style="padding:12px;border:1px solid #d9e8e4;border-radius:8px">'
                                 f'<div style="font-size:12px;color:#5b6b67">{label}</div><div style="font-size:22px;font-weight:700">{value}</div></td>')
    html = f"""<div style="font-family:Inter,Arial,sans-serif;max-width:640px;color:#14211e">
<h2 style="color:#0f5c4d">AI Operations - daily report</h2><p style="color:#5b6b67">{day} - Harbour &amp; Lane</p>
<table style="border-spacing:8px;width:100%"><tr>{card('Messages', m['messages_in'])}{card('Auto-resolved', auto)}{card('Escalations', m['escalations'])}</tr>
<tr>{card('Refunds issued', f"£{m['refunds_issued']['total_gbp']:.2f}")}{card('Avg response', f"{m['latency_ms']['avg'] / 1000:.1f}s")}{card('Eval score', eval_txt)}</tr></table>
<h3>Top issues</h3><ol>{issues}</ol>
<h3>AI accuracy</h3><p>{acc_txt}. Edited: {acc['edited']}, rejected: {acc['rejected']}.</p>
<h3>Cost</h3><p>${m['cost_usd']['total']:.4f} total, ${m['cost_usd']['per_conversation']:.5f} per conversation, {m['tokens']:,} tokens.</p>
<p style="font-size:12px;color:#5b6b67">Open the dashboard: {settings.public_base_url}</p></div>"""
    return text, html


def send_daily_report() -> dict:
    from . import notify
    db = SessionLocal()
    try:
        until = utcnow()
        m = compute_metrics(db, until - timedelta(days=1), until)
        day = (until - timedelta(days=1)).strftime("%A %d %B %Y")
        text, html = render_report(m, day)
        recipients = [r.strip() for r in settings.report_recipients.split(",") if r.strip()]
        if not recipients:
            raise notify.NotConfigured("REPORT_RECIPIENTS not set")
        for r in recipients:
            notify.send_email(r, f"AI Ops daily report - {day}", text, html)
        try:
            notify.slack(f":bar_chart: Daily report sent to {len(recipients)} recipient(s). "
                         f"{m['messages_in']} msgs, auto-resolved {m['auto_resolution_rate']}, refunds £{m['refunds_issued']['total_gbp']:.2f}")
        except Exception:
            pass
        return m
    finally:
        db.close()
