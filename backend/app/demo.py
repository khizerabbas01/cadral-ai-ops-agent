"""Sample traffic for demo environments.

`load_sample_traffic()` replays sample customer messages through the normal pipeline (router -> agent -> tools ->
database) so a fresh install has a history to chart. By default the agents run in offline mode for this - their
deterministic fallbacks, no LLM calls - so loading is quick and costs nothing; pass live=True to use the configured
LLM instead. Timestamps are spread over the past days. No human reviews are invented, and nothing is sent anywhere:
delivery, Slack and email jobs are dropped for the loading thread only. While this data is present the dashboard
shows an "Includes replayed sample traffic" note.
"""
from __future__ import annotations

import json
import random
import time
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update

from . import jobs, llm, runtime_settings
from .config import BASE_DIR, force_offline
from .db import SessionLocal
from .models import AuditLog, Conversation, Customer, Escalation, LLMCall, Message, Order, Refund, Trace, utcnow
from .orchestrator import ingest, process_inbound

SUPPRESSED = {"process_inbound", "deliver_reply", "slack_alert", "send_email", "distil_rule"}
EXTRA_SUPPORT = [
    "Do you deliver to Northern Ireland?", "Can I pay with Apple Pay?", "How do I start a return?",
    "Is the stoneware dinner set dishwasher safe?", "What happens if my parcel is marked delivered but I don't have it?",
    "Can I add an item to an order I've already placed?", "How long does furniture delivery take?",
    "Do gold members get free next-day delivery?", "Can I use two discount codes on one order?",
    "How do I unsubscribe from marketing emails?", "Where is your oak furniture made?", "How should I wash linen bedding?",
]
LOOKUPS = ["Where is my order {id}?", "Can you give me an update on {id}?", "Has order {id} been dispatched yet?",
           "When will {id} arrive?", "Could I have the tracking number for {id} please?", "Any news on {id}? Thanks"]


def _pool(db, rng: random.Random) -> dict[str, list]:
    cases = [json.loads(line) for line in (BASE_DIR / "evals" / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    emails = {c.id: c.email for c in db.scalars(select(Customer)) if c.email and c.email.endswith("@example.co.uk")}
    return {
        "support": [{"text": c["text"]} for c in cases if c["cat"] == "support"] + [{"text": t} for t in EXTRA_SUPPORT],
        "lookup": [{"text": rng.choice(LOOKUPS).format(id=o.id), "email": emails[o.customer_id]}
                   for o in db.scalars(select(Order)) if o.customer_id in emails],
        "action": [c for c in cases if c["cat"] == "orders" and c.get("db")],  # refunds, cancellations, address changes: once each
        "escalation": [c for c in cases if c["cat"] == "escalation"],
        "idk": [c for c in cases if c["cat"] == "idk"],
        "emails": list(emails.values()),
    }


def _schedule(n: int, days: int, rng: random.Random) -> list[datetime]:
    """n timestamps over the last `days` days: fewer at weekends, busier in the daytime, slightly growing."""
    now = utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    weights = []
    for i in range(days):  # i = 0 is the oldest day
        day = midnight - timedelta(days=days - 1 - i)
        weights.append((0.75 + 0.5 * i / max(1, days - 1)) * (0.7 if day.weekday() >= 5 else 1.0))
    out = []
    for i in rng.choices(range(days), weights=weights, k=n):
        day = midnight - timedelta(days=days - 1 - i)
        ts = day + timedelta(hours=min(22.5, max(7.0, rng.gauss(13.5, 3.6))), minutes=rng.random() * 3)
        if ts > now - timedelta(minutes=2):  # today: never in the future
            ts = midnight + (now - timedelta(minutes=2) - midnight) * rng.random()
        out.append(ts)
    return sorted(out)


def load_sample_traffic(*, days: int = 14, conversations: int = 150, seed: int = 7, delay: float = 0.0,
                        live: bool = False, force: bool = False, log=print) -> dict:
    """Returns {"loaded": n, "routes": {...}, "skipped": n}, or {"loaded": 0, "reason": ...} when it did nothing."""
    rng = random.Random(seed)
    db = SessionLocal()
    try:
        if not force:
            if runtime_settings.get_flag(db, "demo_seeded_at"):
                return {"loaded": 0, "reason": "sample traffic is already loaded"}
            if db.scalar(select(func.count()).select_from(Conversation)):
                return {"loaded": 0, "reason": "the database already has conversations"}
        force_offline(not live)
        jobs.suppress_for_this_thread(SUPPRESSED)
        pool = _pool(db, rng)
        actions, escalations = pool["action"][:], pool["escalation"][:]
        rng.shuffle(actions)
        rng.shuffle(escalations)
        routes, skipped = Counter(), 0
        log(f"Replaying {conversations} sample conversations over {days} days ({'live LLM' if live else 'offline mode'}) ...")

        for when in _schedule(conversations, days, rng):
            kind = rng.choices(["support", "lookup", "action", "escalation", "idk"], weights=[48, 27, 8, 13, 4])[0]
            if kind == "action" and not actions:
                kind = "lookup"
            if kind == "action":
                case = actions.pop()
            elif kind == "escalation":
                case = escalations.pop() if escalations else rng.choice(pool["escalation"])
            else:
                case = rng.choice(pool[kind])
            email = case.get("email") or (rng.choice(pool["emails"]) if rng.random() < 0.8 else None)
            channel = rng.choices(["web", "email", "telegram"], weights=[60, 28, 12])[0] if email else "web"
            t0 = utcnow()
            conv, msg = ingest(db, channel=channel, text=case["text"], email=email, subject=case["text"][:80])
            try:
                process_inbound(msg.id)
            except llm.LLMError as e:  # provider down or rate limited: drop this one rather than record a fake outage
                db.execute(delete(Trace).where(Trace.conversation_id == conv.id))
                db.execute(delete(Message).where(Message.conversation_id == conv.id))
                db.execute(delete(Conversation).where(Conversation.id == conv.id))
                db.commit()
                skipped += 1
                log(f"  skipped one message ({str(e)[:80]})")
                time.sleep(max(delay, 5))
                continue
            db.expire_all()
            trace = db.scalar(select(Trace).where(Trace.conversation_id == conv.id))
            routes[trace.route if trace else "?"] += 1
            answered = when + timedelta(milliseconds=(trace.latency_ms if trace else 0) + 400)
            # move everything this message created back to its place in the timeline
            db.execute(update(Conversation).where(Conversation.id == conv.id).values(created_at=when, updated_at=answered))
            db.execute(update(Message).where(Message.conversation_id == conv.id, Message.role == "customer").values(created_at=when))
            db.execute(update(Message).where(Message.conversation_id == conv.id, Message.role != "customer").values(created_at=answered))
            db.execute(update(Trace).where(Trace.conversation_id == conv.id).values(created_at=when))
            if trace:
                db.execute(update(LLMCall).where(LLMCall.trace_id == trace.id).values(created_at=when))
            db.execute(update(Escalation).where(Escalation.conversation_id == conv.id).values(created_at=answered))
            db.execute(update(Refund).where(Refund.created_at >= t0).values(created_at=answered))
            db.execute(update(AuditLog).where(AuditLog.created_at >= t0).values(created_at=answered))
            db.commit()
            if delay:
                time.sleep(delay)

        # Older sample conversations are closed, as a support team would have done; recent ones stay open.
        now = utcnow()
        db.execute(update(Escalation).where(Escalation.created_at < now - timedelta(days=2), Escalation.status == "open")
                   .values(status="resolved"))
        still_open = select(Escalation.conversation_id).where(Escalation.status != "resolved")
        for c in db.scalars(select(Conversation).where(Conversation.created_at < now - timedelta(days=1),
                                                       Conversation.status != "resolved",
                                                       Conversation.id.not_in(still_open))):
            db.execute(update(Conversation).where(Conversation.id == c.id).values(status="resolved", updated_at=c.updated_at))
        runtime_settings.set_flag(db, "demo_seeded_at", now.isoformat() + "Z")
        db.commit()
        log(f"Sample traffic loaded: {sum(routes.values())} conversations {dict(routes)}, {skipped} skipped.")
        return {"loaded": sum(routes.values()), "routes": dict(routes), "skipped": skipped}
    finally:
        force_offline(False)
        jobs.suppress_for_this_thread(set())
        db.close()
