from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import events, jobs, learning, reporting, runtime_settings
from .auth import create_token, current_user, require_admin, verify_password
from .dashboard_api import router as dashboard_router
from .channels import handle_email, handle_telegram_update
from .config import BASE_DIR, settings
from .db import get_db
from .models import (AuditLog, Conversation, Customer, Escalation, EvalRun, Feedback, Job, LLMCall, Message, Order,
                     PromptRule, Refund, Trace, User, utcnow)
from .orchestrator import enqueue_delivery, ingest, serialize_message, serialize_trace
from .rag import kb
from .seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed()
    runtime_settings.load()  # admin-changed settings (reply mode, refund limit) override the env defaults
    threading.Thread(target=kb.build, name="kb-build", daemon=True).start()
    jobs.start_workers(3)
    jobs.start_scheduler()
    import os
    if not os.environ.get("JWT_SECRET"):
        log.warning("JWT_SECRET not set - using a random per-process secret (logins reset on restart)")
    yield
    jobs.stop_scheduler()
    jobs.stop_workers()


app = FastAPI(title="Cadral AI Ops Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    try:  # system errors -> Slack (through the retrying job queue)
        from .db import session_scope
        with session_scope() as s:
            jobs.enqueue(s, "slack_alert", {"kind": "error", "text": f":boom: API error on {request.method} {request.url.path}: {str(exc)[:200]}"})
    except Exception:
        pass
    return JSONResponse({"detail": "Internal error"}, status_code=500)


# ====================================================================== public
class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"token": create_token(user), "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role}}


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    return {"ok": True, "llm": {"provider": settings.llm_provider, "enabled": settings.llm_enabled, "model": settings.llm_model,
                                "fast_model": settings.llm_fast_model},
            "rag": kb.stats() if kb.ready else {"status": "indexing"},
            "jobs": dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all()),
            "reply_mode": settings.reply_mode,
            "integrations": {"slack": bool(settings.slack_webhook_url), "smtp": bool(settings.smtp_host),
                             "imap": bool(settings.imap_host), "telegram": bool(settings.telegram_bot_token)}}


# ---- web chat widget (customer side) ---------------------------------------
def _chat_token(conv_id: int) -> str:
    return jwt.encode({"conv": conv_id, "exp": utcnow() + timedelta(days=7)}, settings.jwt_secret, algorithm="HS256")


def _check_chat_token(conv_id: int, token: str | None) -> None:
    try:
        if jwt.decode(token or "", settings.jwt_secret, algorithms=["HS256"]).get("conv") == conv_id:
            return
    except jwt.PyJWTError:
        pass
    raise HTTPException(403, "Invalid chat token")


_rate: dict[str, list[float]] = defaultdict(list)


def _rate_limit(key: str, limit: int = 20, window: float = 60.0) -> None:
    import time
    now = time.time()
    hits = [t for t in _rate[key] if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Too many messages - please slow down")
    hits.append(now)
    _rate[key] = hits


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None
    token: str | None = None
    email: str | None = None
    name: str | None = None


@app.post("/api/chat/send")
def chat_send(body: ChatIn, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request.client.host if request.client else "anon")
    if body.conversation_id:
        _check_chat_token(body.conversation_id, body.token)
    conv, msg = ingest(db, channel="web", text=body.text, conversation_id=body.conversation_id,
                       email=(body.email or None), name=body.name)
    return {"conversation_id": conv.id, "token": body.token or _chat_token(conv.id), "message": serialize_message(msg)}


@app.get("/api/chat/{conv_id}/messages")
def chat_messages(conv_id: int, token: str, db: Session = Depends(get_db)):
    _check_chat_token(conv_id, token)
    msgs = db.scalars(select(Message).where(Message.conversation_id == conv_id, Message.status == "sent").order_by(Message.id))
    return [serialize_message(m) for m in msgs]


async def _sse(topic: str, request: Request, filt=None):
    q = events.subscribe(topic)
    try:
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                ev = await asyncio.to_thread(q.get, True, 15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if filt:
                ev = filt(ev)
                if ev is None:
                    continue
            yield f"data: {json.dumps(ev, default=str)}\n\n"
    finally:
        events.unsubscribe(topic, q)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def _customer_filter(ev: dict):
    """Customers see tokens and *sent* messages only - never traces or replies held for review."""
    if ev.get("type") == "token" or ev.get("type") == "typing":
        return ev
    if ev.get("type") == "message" and ev["message"]["status"] == "sent":
        return {"type": "message", "message": ev["message"]}
    return None


@app.get("/api/chat/{conv_id}/stream")
async def chat_stream(conv_id: int, token: str, request: Request):
    _check_chat_token(conv_id, token)
    return StreamingResponse(_sse(events.conv_topic(conv_id), request, _customer_filter), media_type="text/event-stream", headers=SSE_HEADERS)


# ---- channel webhooks --------------------------------------------------------
@app.post("/api/channels/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(403, "bad secret")
    update = await request.json()
    await asyncio.to_thread(handle_telegram_update, update)
    return {"ok": True}


class EmailIn(BaseModel):
    from_: str = Field(alias="from")
    subject: str = ""
    text: str
    message_id: str | None = None
    in_reply_to: str | None = None


@app.post("/api/channels/email/inbound")
def email_inbound(body: EmailIn, x_inbound_secret: str | None = Header(default=None)):
    """Inbound-parse webhook (Mailgun/SendGrid/Cloudflare Email Workers forward here)."""
    if settings.inbound_email_secret and x_inbound_secret != settings.inbound_email_secret:
        raise HTTPException(403, "bad secret")
    return {"conversation_id": handle_email(body.from_, body.subject, body.text, body.message_id, body.in_reply_to)}


# ====================================================================== dashboard (auth)
@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


@app.get("/api/stream")
async def stream_all(request: Request, user: User = Depends(current_user)):
    return StreamingResponse(_sse("*", request), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/conversations/{conv_id}/stream")
async def stream_conv(conv_id: int, request: Request, user: User = Depends(current_user)):
    return StreamingResponse(_sse(events.conv_topic(conv_id), request), media_type="text/event-stream", headers=SSE_HEADERS)


def _conv_row(c: Conversation, last: Message | None, pending: int) -> dict:
    return {"id": c.id, "channel": c.channel, "status": c.status, "subject": c.subject, "last_agent": c.last_agent,
            "customer": {"id": c.customer.id, "name": c.customer.name, "email": c.customer.email, "tier": c.customer.tier} if c.customer else None,
            "last_message": last.content[:140] if last else "", "pending_review": pending,
            "updated_at": c.updated_at.isoformat() + "Z", "created_at": c.created_at.isoformat() + "Z"}


@app.get("/api/conversations")
def list_conversations(status: str | None = None, channel: str | None = None, needs_review: bool = False,
                       limit: int = 100, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(Conversation).order_by(Conversation.updated_at.desc()).limit(min(limit, 300))
    if status:
        q = q.where(Conversation.status == status)
    if channel:
        q = q.where(Conversation.channel == channel)
    convs = db.scalars(q).all()
    ids = [c.id for c in convs]
    pend = dict(db.execute(select(Message.conversation_id, func.count()).where(Message.conversation_id.in_(ids), Message.status == "pending_review")
                           .group_by(Message.conversation_id)).all()) if ids else {}
    last_ids = dict(db.execute(select(Message.conversation_id, func.max(Message.id)).where(Message.conversation_id.in_(ids))
                               .group_by(Message.conversation_id)).all()) if ids else {}
    lasts = {m.conversation_id: m for m in db.scalars(select(Message).where(Message.id.in_(list(last_ids.values()))))} if last_ids else {}
    rows = [_conv_row(c, lasts.get(c.id), pend.get(c.id, 0)) for c in convs]
    if needs_review:
        rows = [r for r in rows if r["pending_review"]]
    return rows


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404)
    msgs = db.scalars(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)).all()
    traces = db.scalars(select(Trace).where(Trace.conversation_id == conv_id).order_by(Trace.id)).all()
    escs = db.scalars(select(Escalation).where(Escalation.conversation_id == conv_id).order_by(Escalation.id)).all()
    orders = []
    if c.customer_id:
        orders = [{"id": o.id, "status": o.status, "total_gbp": o.total_gbp, "refunded_gbp": o.refunded_gbp}
                  for o in db.scalars(select(Order).where(Order.customer_id == c.customer_id).order_by(Order.created_at.desc()).limit(8))]
    fb = {f.message_id: {"action": f.action, "reason": f.reason} for f in
          db.scalars(select(Feedback).where(Feedback.message_id.in_([m.id for m in msgs])))} if msgs else {}
    return {"conversation": _conv_row(c, msgs[-1] if msgs else None, sum(m.status == "pending_review" for m in msgs)) | {"summary": c.summary},
            "customer_notes": c.customer.notes if c.customer else "",
            "messages": [serialize_message(m) | {"feedback": fb.get(m.id)} for m in msgs],
            "traces": [serialize_trace(t) for t in traces],
            "escalations": [_esc(e) for e in escs], "orders": orders}


def _esc(e: Escalation) -> dict:
    return {"id": e.id, "conversation_id": e.conversation_id, "severity": e.severity, "reason": e.reason, "summary": e.summary,
            "status": e.status, "assigned_to": e.assigned_to, "sentiment": e.sentiment, "created_at": e.created_at.isoformat() + "Z"}


class ReviewIn(BaseModel):
    action: str  # approve | edit | reject
    text: str | None = None
    reason: str = ""


@app.post("/api/messages/{msg_id}/review")
def review_message(msg_id: int, body: ReviewIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = db.get(Message, msg_id)
    if not m or m.role != "assistant":
        raise HTTPException(404, "AI message not found")
    if body.action not in {"approve", "edit", "reject"}:
        raise HTTPException(400, "action must be approve, edit or reject")
    if body.action == "edit" and not (body.text and body.text.strip()):
        raise HTTPException(400, "edited text required")
    inbound = db.get(Message, m.reply_to_id) if m.reply_to_id else None
    fb = Feedback(message_id=m.id, action=body.action, agent=m.agent, customer_message=inbound.content if inbound else "",
                  original_reply=m.content, reason=body.reason, user_id=user.id,
                  final_reply=(body.text.strip() if body.action == "edit" else (m.content if body.action == "approve" else None)))
    db.add(fb)
    was_pending = m.status == "pending_review"
    m.reviewed = True
    out_msg = None
    if body.action == "approve" and was_pending:
        m.status = "sent"
        out_msg = m
    elif body.action == "edit":
        if was_pending:
            m.content, m.status = body.text.strip(), "sent"
            out_msg = m
        else:  # already sent: supersede and send the correction
            m.status = "superseded"
            out_msg = Message(conversation_id=m.conversation_id, role="human", content=body.text.strip(), agent=f"human:{user.name}",
                              status="sent", reply_to_id=m.reply_to_id, reviewed=True)
            db.add(out_msg)
    elif body.action == "reject" and was_pending:
        m.status = "rejected"
    db.flush()
    if out_msg is not None:
        enqueue_delivery(db, out_msg)
    if body.action in {"edit", "reject"}:
        jobs.enqueue(db, "distil_rule", {"feedback_id": fb.id}, max_attempts=3)
    db.commit()
    for mm in {m, out_msg} - {None}:
        events.publish(events.conv_topic(m.conversation_id), {"type": "message", "message": serialize_message(mm)})
    events.publish("*", {"type": "conversation_updated", "conversation_id": m.conversation_id})
    return {"ok": True, "feedback_id": fb.id}


class HumanReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/conversations/{conv_id}/reply")
def human_reply(conv_id: int, body: HumanReplyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404)
    m = Message(conversation_id=c.id, role="human", content=body.text.strip(), agent=f"human:{user.name}", status="sent", reviewed=True)
    db.add(m)
    c.updated_at = utcnow()
    db.flush()
    enqueue_delivery(db, m)
    db.commit()
    events.publish(events.conv_topic(c.id), {"type": "message", "message": serialize_message(m)})
    events.publish("*", {"type": "conversation_updated", "conversation_id": c.id})
    return serialize_message(m)


@app.get("/api/escalations")
def list_escalations(status: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(Escalation).order_by(Escalation.id.desc()).limit(200)
    if status:
        q = q.where(Escalation.status == status)
    return [_esc(e) for e in db.scalars(q)]


@app.post("/api/escalations/{esc_id}/{action}")
def update_escalation(esc_id: int, action: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    e = db.get(Escalation, esc_id)
    if not e or action not in {"claim", "resolve"}:
        raise HTTPException(404)
    if action == "claim":
        e.status, e.assigned_to = "claimed", user.id
    else:
        e.status = "resolved"
        conv = db.get(Conversation, e.conversation_id)
        if not db.scalar(select(Escalation.id).where(Escalation.conversation_id == conv.id, Escalation.status != "resolved", Escalation.id != e.id)):
            conv.status = "resolved"
    db.commit()
    events.publish("*", {"type": "conversation_updated", "conversation_id": e.conversation_id})
    return _esc(e)


@app.post("/api/conversations/{conv_id}/resolve")
def resolve_conversation(conv_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404)
    c.status = "resolved"
    for e in db.scalars(select(Escalation).where(Escalation.conversation_id == conv_id, Escalation.status != "resolved")):
        e.status = "resolved"
    db.commit()
    events.publish("*", {"type": "conversation_updated", "conversation_id": conv_id})
    return {"ok": True}


# ---- analytics -------------------------------------------------------------
@app.get("/api/analytics")
def analytics(days: int = 14, db: Session = Depends(get_db), user: User = Depends(current_user)):
    days = max(1, min(days, 90))
    since = utcnow() - timedelta(days=days)
    summary = reporting.compute_metrics(db, since)
    traces = db.scalars(select(Trace).where(Trace.created_at >= since)).all()
    fbs = db.scalars(select(Feedback).where(Feedback.created_at >= since)).all()
    series: dict[str, dict] = {}
    for i in range(days + 1):
        d = (since + timedelta(days=i)).strftime("%Y-%m-%d")
        series[d] = {"date": d, "messages": 0, "resolved": 0, "latency_sum": 0, "cost": 0.0, "convs": set(), "approved": 0, "reviewed": 0}
    for t in traces:
        s = series.get(t.created_at.strftime("%Y-%m-%d"))
        if s:
            s["messages"] += 1
            s["resolved"] += int(t.resolved_by_ai)
            s["latency_sum"] += t.latency_ms
            s["cost"] += t.cost_usd
            s["convs"].add(t.conversation_id)
    for f in fbs:
        s = series.get(f.created_at.strftime("%Y-%m-%d"))
        if s:
            s["reviewed"] += 1
            s["approved"] += int(f.action == "approve")
    out = []
    for s in series.values():
        n = s["messages"]
        out.append({"date": s["date"], "messages": n,
                    "auto_resolution": round(s["resolved"] / n, 3) if n else None,
                    "avg_latency_ms": int(s["latency_sum"] / n) if n else None,
                    "cost_per_conversation": round(s["cost"] / len(s["convs"]), 6) if s["convs"] else None,
                    "accuracy": round(s["approved"] / s["reviewed"], 3) if s["reviewed"] else None})
    evals = [{"date": e.created_at.isoformat(), "score": e.score, "routing_accuracy": e.routing_accuracy}
             for e in db.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(20))][::-1]
    model_costs = [{"model": m, "calls": n, "cost_usd": round(c or 0, 5), "avg_latency_ms": int(l or 0)} for m, n, c, l in db.execute(
        select(LLMCall.model, func.count(), func.sum(LLMCall.cost_usd), func.avg(LLMCall.latency_ms))
        .where(LLMCall.created_at >= since).group_by(LLMCall.model)).all()]
    return {"summary": summary, "series": out, "evals": evals, "models": model_costs}


# ---- orders / refunds / customers -------------------------------------------
@app.get("/api/orders")
def list_orders(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(select(Order, Customer.name).join(Customer).order_by(Order.id)).all()
    return [{"id": o.id, "customer": n, "status": o.status, "total_gbp": o.total_gbp, "refunded_gbp": o.refunded_gbp,
             "items": o.items, "carrier": o.carrier, "updated_at": o.updated_at.isoformat() + "Z"} for o, n in rows]


@app.get("/api/refunds")
def list_refunds(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [{"id": r.id, "order_id": r.order_id, "amount_gbp": r.amount_gbp, "reason": r.reason, "status": r.status,
             "issued_by": r.issued_by, "created_at": r.created_at.isoformat() + "Z"}
            for r in db.scalars(select(Refund).order_by(Refund.id.desc()).limit(200))]


@app.post("/api/refunds/{refund_id}/{action}")
def decide_refund(refund_id: int, action: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    r = db.get(Refund, refund_id)
    if not r or r.status != "pending_approval" or action not in {"approve", "reject"}:
        raise HTTPException(400, "refund not pending")
    o = db.get(Order, r.order_id)
    if action == "approve":
        r.status = "issued"
        o.refunded_gbp = round(o.refunded_gbp + r.amount_gbp, 2)
        o.status = "refunded" if o.refunded_gbp >= o.total_gbp - 0.01 else "partially_refunded"
    else:
        r.status = "rejected"
    db.add(AuditLog(entity="refund", entity_id=str(r.id), action=f"refund_{action}d", details={"amount_gbp": r.amount_gbp}, actor=f"human:{user.name}"))
    db.commit()
    return {"ok": True, "status": r.status}


@app.get("/api/audit")
def audit(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [{"id": a.id, "entity": a.entity, "entity_id": a.entity_id, "action": a.action, "details": a.details, "actor": a.actor,
             "created_at": a.created_at.isoformat() + "Z"} for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(100))]


# ---- learning loop -----------------------------------------------------------
@app.get("/api/learning")
def learning_state(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rules = db.scalars(select(PromptRule).order_by(PromptRule.id.desc())).all()
    fbs = db.scalars(select(Feedback).order_by(Feedback.id.desc()).limit(100)).all()
    return {"rules": [{"id": r.id, "agent": r.agent, "rule": r.rule, "active": r.active, "source_feedback_id": r.source_feedback_id,
                       "created_at": r.created_at.isoformat() + "Z"} for r in rules],
            "feedback": [{"id": f.id, "message_id": f.message_id, "action": f.action, "agent": f.agent, "customer_message": f.customer_message,
                          "original_reply": f.original_reply, "final_reply": f.final_reply, "reason": f.reason,
                          "created_at": f.created_at.isoformat() + "Z"} for f in fbs],
            "dataset_size": len(learning.export_dataset(db))}


class RuleIn(BaseModel):
    active: bool | None = None
    rule: str | None = None
    agent: str | None = None


@app.patch("/api/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    r = db.get(PromptRule, rule_id)
    if not r:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(r, k, v)
    db.commit()
    return {"ok": True}


@app.post("/api/rules")
def create_rule(body: RuleIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if not body.rule:
        raise HTTPException(400, "rule required")
    r = PromptRule(agent=body.agent or "all", rule=body.rule)
    db.add(r)
    db.commit()
    return {"id": r.id}


@app.get("/api/learning/dataset.jsonl", response_class=PlainTextResponse)
def export_dataset(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return "\n".join(json.dumps(r) for r in learning.export_dataset(db))


# ---- ops / admin -------------------------------------------------------------
@app.get("/api/jobs")
def list_jobs(status: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    q = select(Job).order_by(Job.id.desc()).limit(100)
    if status:
        q = q.where(Job.status == status)
    return [{"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts,
             "last_error": (j.last_error or "")[:400], "next_run_at": j.next_run_at.isoformat() + "Z",
             "created_at": j.created_at.isoformat() + "Z"} for j in db.scalars(q)]


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(404)
    j.status, j.attempts, j.next_run_at = "pending", 0, utcnow()
    db.commit()
    jobs._wake.set()
    return {"ok": True}


@app.get("/api/admin/report/preview")
def report_preview(db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = reporting.compute_metrics(db, utcnow() - timedelta(days=1))
    text, html = reporting.render_report(m, "last 24 hours")
    return {"metrics": m, "text": text, "html": html}


@app.post("/api/admin/report/send")
def report_send(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    j = jobs.enqueue(db, "daily_report", {}, max_attempts=4)
    db.commit()
    return {"queued_job": j.id}


@app.post("/api/admin/reindex")
def reindex(user: User = Depends(require_admin)):
    return kb.build(force=True)


@app.get("/api/kb/search")
def kb_search(q: str, user: User = Depends(current_user)):
    """Retrieval playground: the ranked passages plus the confidence-gate verdict the Support agent would reach."""
    hits = kb.search(q, k=5)
    return {"hits": [h.to_dict() for h in hits], "confident": kb.confident(hits), "threshold": kb.threshold,
            "top_score": round(hits[0].score, 4) if hits else 0.0}


@app.get("/api/kb/documents")
def kb_documents(user: User = Depends(current_user)):
    chunks, titles = {}, {}
    for c in kb.chunks:
        chunks[c.doc] = chunks.get(c.doc, 0) + 1
        titles.setdefault(c.doc, c.title)
    docs = [{"name": p.name, "type": p.suffix.lstrip(".").lower(), "size": p.stat().st_size,
             "chunks": chunks.get(p.name, 0), "title": titles.get(p.name, p.stem)} for p in settings.docs_dir.iterdir()]
    return {"documents": sorted(docs, key=lambda d: d["name"]), "index": kb.stats() if kb.ready else None,
            "threshold": kb.threshold}


class SimEmailIn(BaseModel):
    from_email: str
    name: str = ""
    subject: str
    text: str


@app.post("/api/demo/simulate-email")
def simulate_email(body: SimEmailIn, user: User = Depends(current_user)):
    """Demo helper: inject an email exactly as the IMAP poller would."""
    conv_id = handle_email(f"{body.name} <{body.from_email}>" if body.name else body.from_email, body.subject, body.text,
                           message_id=f"<sim-{utcnow().timestamp()}@demo>")
    return {"conversation_id": conv_id}


@app.get("/api/customers")
def customers(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [{"id": c.id, "name": c.name, "email": c.email, "tier": c.tier} for c in db.scalars(select(Customer).order_by(Customer.id))]


app.include_router(dashboard_router)  # must be registered before the SPA catch-all below


# ====================================================================== frontend (SPA)
class RevalidatedStatic(StaticFiles):
    """Static files with `Cache-Control: no-cache`: browsers revalidate (cheap 304s) so a deploy is picked up at once."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


DIST = BASE_DIR.parent / "frontend"  # dependency-free SPA: no build step needed
if DIST.exists():
    app.mount("/assets", RevalidatedStatic(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Unknown API endpoint")
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
