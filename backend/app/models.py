from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="agent")  # admin | agent
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200), unique=True, index=True, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    tier: Mapped[str] = mapped_column(String(20), default="standard")  # standard | vip
    notes: Mapped[str] = mapped_column(Text, default="")  # long-term memory written by agents
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80))
    price_gbp: Mapped[float] = mapped_column(Float)
    stock: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # e.g. UK-10042
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="processing")
    # processing | dispatched | in_transit | delivered | cancelled | refunded | partially_refunded
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{sku, name, qty, price_gbp}]
    total_gbp: Mapped[float] = mapped_column(Float)
    refunded_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    shipping_address: Mapped[str] = mapped_column(Text)
    carrier: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    estimated_delivery: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="orders")


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount_gbp: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="issued")  # issued | pending_approval | rejected
    issued_by: Mapped[str] = mapped_column(String(60), default="orders_agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    """Every state-changing tool call is written here - the 'records' the Orders agent updates."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(60), index=True)
    action: Mapped[str] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True, nullable=True)
    channel: Mapped[str] = mapped_column(String(20))  # web | email | telegram
    external_ref: Mapped[str | None] = mapped_column(String(200), index=True, nullable=True)
    subject: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | escalated | resolved
    last_agent: Mapped[str | None] = mapped_column(String(30), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")  # rolling summary = shared short-term memory
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer | None] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", order_by="Message.id")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # customer | assistant | human | system
    content: Mapped[str] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent | pending_review | rejected | superseded
    citations: Mapped[list] = mapped_column(JSON, default=list)
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("traces.id"), nullable=True)
    reply_to_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Trace(Base):
    """One trace per inbound message: which agent, which tools, why, cost and latency."""
    __tablename__ = "traces"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    inbound_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    route: Mapped[str] = mapped_column(String(30), default="")
    route_reason: Mapped[str] = mapped_column(Text, default="")
    route_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    resolved_by_ai: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    purpose: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Escalation(Base):
    __tablename__ = "escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # low | medium | high | critical
    reason: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)  # -1..1
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | claimed | resolved
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Feedback(Base):
    """Human corrections captured from the dashboard - fuel for the self-improving loop."""
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    action: Mapped[str] = mapped_column(String(10))  # approve | edit | reject
    agent: Mapped[str | None] = mapped_column(String(30), nullable=True)
    customer_message: Mapped[str] = mapped_column(Text, default="")
    original_reply: Mapped[str] = mapped_column(Text, default="")
    final_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_as_example: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PromptRule(Base):
    """Guidelines distilled from rejected/edited replies and injected into agent prompts."""
    __tablename__ = "prompt_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(30))  # support | orders | escalation | all
    rule: Mapped[str] = mapped_column(Text)
    source_feedback_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    """Durable job queue / outbox. Inbound messages and outbound sends are persisted before any
    external API is touched, so nothing is lost if an API fails - jobs are retried with backoff."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|running|done|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    routing_accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """Key/value store for settings an admin changes at runtime (dashboard -> Settings) and for flags
    such as 'demo traffic loaded'. Editable keys override the environment defaults in config.py."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
