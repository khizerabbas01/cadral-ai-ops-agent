"""Pipeline: inbound message (any channel) -> persisted -> job -> router -> specialist agent -> reply
(sent or held for review) -> outbound delivery job. Tokens and trace steps are streamed to the dashboard."""
from __future__ import annotations

import logging
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import events, llm
from .agents import AgentResult, Trace, escalation_agent, orders_agent, route, support_agent
from .config import settings
from .db import SessionLocal
from .jobs import enqueue
from .memory import build_memory
from .models import Conversation, Customer, Escalation, LLMCall, Message
from .models import Trace as TraceRow
from .models import utcnow

log = logging.getLogger("orchestrator")


# ------------------------------------------------------------------ inbound
def find_or_create_customer(db: Session, *, email: str | None = None, name: str | None = None,
                            telegram_chat_id: str | None = None) -> Customer | None:
    c = None
    if email:
        c = db.scalar(select(Customer).where(func.lower(Customer.email) == email.strip().lower()))
    if not c and telegram_chat_id:
        c = db.scalar(select(Customer).where(Customer.telegram_chat_id == str(telegram_chat_id)))
    if not c and (email or telegram_chat_id):
        c = Customer(name=name or (email.split("@")[0] if email else "Telegram user"), email=email.lower() if email else None,
                     telegram_chat_id=str(telegram_chat_id) if telegram_chat_id else None)
        db.add(c)
        db.flush()
    elif c and telegram_chat_id and not c.telegram_chat_id:
        c.telegram_chat_id = str(telegram_chat_id)
    return c


def ingest(db: Session, *, channel: str, text: str, conversation_id: int | None = None, email: str | None = None,
           name: str | None = None, telegram_chat_id: str | None = None, external_ref: str | None = None,
           subject: str = "") -> tuple[Conversation, Message]:
    """Persist first, process later: the message is durable before any LLM/API call happens."""
    conv = db.get(Conversation, conversation_id) if conversation_id else None
    if conv is None and external_ref:
        conv = db.scalar(select(Conversation).where(Conversation.external_ref == external_ref, Conversation.status != "resolved")
                         .order_by(Conversation.id.desc()))
    customer = find_or_create_customer(db, email=email, name=name, telegram_chat_id=telegram_chat_id)
    if conv is None:
        conv = Conversation(channel=channel, customer_id=customer.id if customer else None, external_ref=external_ref,
                            subject=(subject or text)[:120])
        db.add(conv)
        db.flush()
    elif customer and not conv.customer_id:
        conv.customer_id = customer.id
    if conv.status == "resolved":
        conv.status = "open"
    msg = Message(conversation_id=conv.id, role="customer", content=text.strip()[:4000])
    db.add(msg)
    conv.updated_at = utcnow()
    db.flush()
    enqueue(db, "process_inbound", {"message_id": msg.id}, max_attempts=4)
    db.commit()
    events.publish(events.conv_topic(conv.id), {"type": "message", "message": serialize_message(msg)})
    events.publish("*", {"type": "conversation_updated", "conversation_id": conv.id})
    return conv, msg


# ------------------------------------------------------------------ processing
def process_inbound(message_id: int, *, final_attempt: bool = False) -> dict | None:
    db = SessionLocal()
    try:
        inbound = db.get(Message, message_id)
        if inbound is None:
            return None
        already = db.scalar(select(Message.id).where(Message.reply_to_id == message_id, Message.role == "assistant"))
        if already:  # idempotent: a retried job never double-replies
            return {"skipped": True}
        conv = db.get(Conversation, inbound.conversation_id)
        topic = events.conv_topic(conv.id)
        trace_row = TraceRow(conversation_id=conv.id, inbound_message_id=inbound.id)
        db.add(trace_row)
        db.commit()
        trace = Trace(trace_row.id, emit=lambda e: events.publish(topic, {**e, "trace_id": trace_row.id}))
        events.publish(topic, {"type": "typing", "trace_id": trace_row.id})
        t0 = time.perf_counter()
        memory = build_memory(db, conv.id, exclude_message_id=inbound.id)
        on_token = lambda tok: events.publish(topic, {"type": "token", "text": tok, "trace_id": trace_row.id})
        try:
            if conv.status == "escalated" and not _human_released(db, conv):
                decision = {"route": "escalation", "reason": "conversation already with a human", "risk": 0.5,
                            "risk_reasons": ["open escalation"], "confidence": 1.0, "method": "state"}
                trace.step("route", "router", "Conversation already escalated - keeping it with the human team", why=decision["reason"])
                result = AgentResult("escalation", "Thanks - I've added this to your case. A member of our team is already "
                                     "looking at it and will reply shortly.", [], escalated=False)
                for w in result.reply.split(" "):
                    on_token(w + " ")
            else:
                decision = route(db, inbound.content, memory, trace)
                if decision["route"] == "orders":
                    result = orders_agent(db, inbound.content, memory, trace, on_token)
                elif decision["route"] == "escalation":
                    result = escalation_agent(db, inbound.content, memory, trace, on_token, decision)
                else:
                    result = support_agent(db, inbound.content, memory, trace, on_token)
        except llm.LLMError as e:
            db.rollback()
            if not final_attempt:
                raise  # job layer retries with backoff - the inbound message is safe in the DB
            log.error("LLM unavailable after retries for message %s: %s", message_id, e)
            trace.step("error", "system", "LLM provider unavailable - sent holding reply and escalated", why=str(e)[:200])
            decision = {"route": "escalation", "reason": "system error", "risk": 0, "risk_reasons": ["system error"]}
            result = AgentResult("escalation", "Thanks for your message - we're experiencing a short delay. "
                                 "A member of our team will reply to you shortly.", [], escalated=True,
                                 meta={"handoff": {"severity": "medium", "reason": "AI unavailable (LLM errors)",
                                                   "summary": f"Message could not be processed automatically: {inbound.content[:300]}",
                                                   "suggested_action": "Reply manually"}})
            trace_row.error = str(e)[:500]
            enqueue(db, "slack_alert", {"kind": "error", "text": f":warning: LLM failure on conversation #{conv.id}: {str(e)[:200]}"})

        status = "pending_review" if (settings.reply_mode == "review" or result.needs_review) else "sent"
        if result.escalated:
            status = "sent"  # the holding message always goes out; the human takes over from here
        reply = Message(conversation_id=conv.id, role="assistant", content=result.reply, agent=result.agent,
                        status=status, citations=result.citations, trace_id=trace_row.id, reply_to_id=inbound.id)
        db.add(reply)
        conv.last_agent = result.agent
        conv.updated_at = utcnow()
        if result.escalated:
            h = result.meta.get("handoff", {})
            conv.status = "escalated"
            esc = Escalation(conversation_id=conv.id, severity=h.get("severity", "medium"), reason=h.get("reason", ""),
                             summary=h.get("summary", ""), sentiment=float(result.meta.get("sentiment") or 0))
            db.add(esc)
            db.flush()
            cust = db.get(Customer, conv.customer_id) if conv.customer_id else None
            enqueue(db, "slack_alert", {"kind": "escalation", "escalation": {
                "id": esc.id, "severity": esc.severity, "reason": esc.reason, "summary": esc.summary,
                "suggested_action": h.get("suggested_action", ""), "channel": conv.channel,
                "customer": cust.name if cust else "Guest"}, "conversation_id": conv.id})
            events.publish("*", {"type": "escalation", "conversation_id": conv.id, "severity": esc.severity})
        db.flush()

        # roll up cost/latency for this trace
        pt, ct, cost = db.execute(select(func.coalesce(func.sum(LLMCall.prompt_tokens), 0), func.coalesce(func.sum(LLMCall.completion_tokens), 0),
                                         func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(LLMCall.trace_id == trace_row.id)).one()
        trace_row.route = decision["route"]
        trace_row.route_reason = decision.get("reason", "")
        trace_row.route_confidence = float(decision.get("confidence") or 0)
        trace_row.steps = trace.steps
        trace_row.prompt_tokens, trace_row.completion_tokens, trace_row.cost_usd = int(pt), int(ct), float(cost)
        trace_row.latency_ms = int((time.perf_counter() - t0) * 1000)
        trace_row.resolved_by_ai = (not result.escalated and status == "sent" and not result.meta.get("idk")
                                    and not result.meta.get("refund_pending_approval"))
        db.commit()
        if status == "sent":
            enqueue_delivery(db, reply)
            db.commit()
        _maybe_summarise(db, conv)
        events.publish(topic, {"type": "message", "message": serialize_message(reply), "trace": serialize_trace(trace_row)})
        events.publish("*", {"type": "conversation_updated", "conversation_id": conv.id})
        return {"reply_id": reply.id, "route": decision["route"], "status": status}
    finally:
        db.close()


def _human_released(db: Session, conv: Conversation) -> bool:
    """Once escalated, the AI stays out until a human resolves the escalation."""
    open_esc = db.scalar(select(Escalation.id).where(Escalation.conversation_id == conv.id, Escalation.status != "resolved"))
    if open_esc is None:
        conv.status = "open"
        return True
    return False


def enqueue_delivery(db: Session, msg: Message) -> None:
    conv = db.get(Conversation, msg.conversation_id)
    if conv.channel in {"email", "telegram"}:
        enqueue(db, "deliver_reply", {"message_id": msg.id}, max_attempts=8)


def deliver_reply(message_id: int) -> None:
    from . import notify
    db = SessionLocal()
    try:
        msg = db.get(Message, message_id)
        conv = db.get(Conversation, msg.conversation_id)
        cust = db.get(Customer, conv.customer_id) if conv.customer_id else None
        if conv.channel == "email" and cust and cust.email:
            body = msg.content
            if msg.citations:
                body += "\n\nSources:\n" + "\n".join(f"[{c['n']}] {c['title']} - {c['section']}" for c in msg.citations)
            body += "\n\n--\nHarbour & Lane Customer Care (AI assistant, supervised by our team)"
            notify.send_email(cust.email, f"Re: {conv.subject}"[:200], body, in_reply_to=conv.external_ref)
        elif conv.channel == "telegram" and cust and cust.telegram_chat_id:
            notify.telegram_send(cust.telegram_chat_id, msg.content)
    finally:
        db.close()


def _maybe_summarise(db: Session, conv: Conversation) -> None:
    n = db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conv.id))
    if n < 8 or n % 4 or not settings.llm_enabled:
        return
    try:
        mem = build_memory(db, conv.id)
        res = llm.chat([{"role": "system", "content": "Summarise this support conversation in 2 sentences for other agents: the customer's issue, what has been done, what is outstanding."},
                        {"role": "user", "content": mem.transcript(30)}], purpose="memory:summary",
                       model=settings.llm_fast_model, max_tokens=120)
        conv.summary = res.content.strip()
        db.commit()
    except Exception:
        log.exception("summary failed")


# ------------------------------------------------------------------ serialisers
def serialize_message(m: Message) -> dict:
    return {"id": m.id, "conversation_id": m.conversation_id, "role": m.role, "content": m.content, "agent": m.agent,
            "status": m.status, "citations": m.citations or [], "trace_id": m.trace_id, "reply_to_id": m.reply_to_id,
            "reviewed": m.reviewed, "created_at": m.created_at.isoformat() + "Z"}


def serialize_trace(t: TraceRow | None) -> dict | None:
    if t is None:
        return None
    return {"id": t.id, "route": t.route, "route_reason": t.route_reason, "route_confidence": t.route_confidence,
            "steps": t.steps, "prompt_tokens": t.prompt_tokens, "completion_tokens": t.completion_tokens,
            "cost_usd": round(t.cost_usd, 6), "latency_ms": t.latency_ms, "resolved_by_ai": t.resolved_by_ai,
            "error": t.error, "created_at": t.created_at.isoformat() + "Z"}
