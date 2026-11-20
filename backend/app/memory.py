"""Shared memory used by every agent:
  * short-term: the conversation transcript (+ rolling summary for long threads)
  * long-term: the customer's profile, notes written by agents, orders and past conversations/escalations."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Conversation, Customer, Escalation, Message, Order

MAX_TURNS = 12


@dataclass
class Memory:
    conversation_id: int
    channel: str
    customer: dict | None
    history: list[dict] = field(default_factory=list)  # [{role, content, agent}]
    summary: str = ""
    profile_text: str = ""

    def chat_messages(self) -> list[dict]:
        """Transcript in OpenAI chat format (customer -> user, everyone else -> assistant)."""
        out = []
        for m in self.history:
            role = "user" if m["role"] == "customer" else "assistant"
            content = m["content"] if role == "user" else (f"[{m.get('agent') or 'human'}] " + m["content"])
            out.append({"role": role, "content": content})
        return out

    def transcript(self, last: int = 8) -> str:
        return "\n".join(f"{'Customer' if m['role'] == 'customer' else (m.get('agent') or 'Agent')}: {m['content']}"
                         for m in self.history[-last:])


def build_memory(db: Session, conversation_id: int, exclude_message_id: int | None = None) -> Memory:
    conv = db.get(Conversation, conversation_id)
    msgs = db.scalars(select(Message).where(Message.conversation_id == conversation_id,
                                            Message.status.in_(["sent", "pending_review"]))
                      .order_by(Message.id)).all()
    history = [{"role": m.role, "content": m.content, "agent": m.agent} for m in msgs if m.id != exclude_message_id]
    customer = None
    profile_lines: list[str] = []
    if conv.customer_id:
        c: Customer = db.get(Customer, conv.customer_id)
        orders = db.scalars(select(Order).where(Order.customer_id == c.id).order_by(Order.created_at.desc()).limit(5)).all()
        past = db.scalars(select(Conversation).where(Conversation.customer_id == c.id, Conversation.id != conv.id)
                          .order_by(Conversation.updated_at.desc()).limit(3)).all()
        escalations = db.scalar(select(Escalation.id).join(Conversation, Escalation.conversation_id == Conversation.id)
                                .where(Conversation.customer_id == c.id).limit(1))
        customer = {"id": c.id, "name": c.name, "email": c.email, "tier": c.tier}
        profile_lines.append(f"Customer: {c.name} <{c.email}> - tier: {c.tier}" + (" (VIP: prioritise)" if c.tier == "vip" else ""))
        if orders:
            profile_lines.append("Recent orders: " + "; ".join(
                f"{o.id} ({o.status}, £{o.total_gbp:.2f}, {', '.join(i['name'] for i in o.items)})" for o in orders))
        if c.notes:
            profile_lines.append("Notes from previous interactions:\n" + c.notes[-800:])
        if past:
            profile_lines.append("Previous conversations: " + "; ".join(
                f"#{p.id} via {p.channel} [{p.status}] {p.summary or p.subject}"[:200] for p in past))
        if escalations:
            profile_lines.append("This customer has been escalated to a human before - be extra careful.")
    else:
        profile_lines.append("Customer not identified yet (guest). Verify identity with order number + email before sharing order details.")
    if len(history) > MAX_TURNS and conv.summary:
        history = history[-MAX_TURNS:]
    return Memory(conversation_id=conv.id, channel=conv.channel, customer=customer, history=history,
                  summary=conv.summary, profile_text="\n".join(profile_lines))
