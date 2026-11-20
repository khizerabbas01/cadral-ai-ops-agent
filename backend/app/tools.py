"""Real database-backed tools used by the Orders agent via function calling.
Every state change is validated, written to the DB and recorded in the audit log."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import AuditLog, Customer, Order, Refund, utcnow

ORDER_ID_RE = re.compile(r"\bUK[-\s]?(\d{5})\b", re.I)


def normalise_order_id(raw: str) -> str:
    m = ORDER_ID_RE.search(raw or "")
    if m:
        return f"UK-{m.group(1)}"
    digits = re.sub(r"\D", "", raw or "")
    return f"UK-{digits}" if len(digits) == 5 else (raw or "").strip().upper()


@dataclass
class ToolContext:
    db: Session
    customer_id: int | None
    actor: str = "orders_agent"


class ToolError(Exception):
    pass


def _order_summary(o: Order) -> dict:
    return {"order_id": o.id, "status": o.status, "items": o.items, "total_gbp": o.total_gbp,
            "refunded_gbp": o.refunded_gbp, "carrier": o.carrier, "tracking_number": o.tracking_number,
            "estimated_delivery": o.estimated_delivery.strftime("%A %d %B %Y") if o.estimated_delivery else None,
            "shipping_address": o.shipping_address, "placed_on": o.created_at.strftime("%d %B %Y")}


def _get_order(ctx: ToolContext, order_id: str, email: str | None = None) -> Order:
    oid = normalise_order_id(order_id)
    order = ctx.db.get(Order, oid)
    if not order:
        raise ToolError(f"No order found with id {oid}.")
    if ctx.customer_id is not None:
        if order.customer_id != ctx.customer_id:
            raise ToolError(f"Order {oid} does not belong to this customer. Ask them to double-check the order number.")
    else:
        owner = ctx.db.get(Customer, order.customer_id)
        if not email or not owner or (owner.email or "").lower() != email.strip().lower():
            raise ToolError("Identity not verified. Ask the customer for the email address used on the order.")
    return order


def _audit(ctx: ToolContext, entity: str, entity_id: str, action: str, details: dict) -> None:
    ctx.db.add(AuditLog(entity=entity, entity_id=entity_id, action=action, details=details, actor=ctx.actor))


# ---------------------------------------------------------------- tool implementations
def get_order_status(ctx: ToolContext, order_id: str, email: str | None = None) -> dict:
    return _order_summary(_get_order(ctx, order_id, email))


def list_customer_orders(ctx: ToolContext, email: str | None = None) -> dict:
    cid = ctx.customer_id
    if cid is None and email:
        c = ctx.db.scalar(select(Customer).where(Customer.email == email.strip().lower()))
        cid = c.id if c else None
    if cid is None:
        raise ToolError("Customer not identified. Ask for the order number or account email.")
    orders = ctx.db.scalars(select(Order).where(Order.customer_id == cid).order_by(Order.created_at.desc()).limit(10))
    return {"orders": [{"order_id": o.id, "status": o.status, "total_gbp": o.total_gbp,
                        "placed_on": o.created_at.strftime("%d %b %Y"),
                        "items": ", ".join(i["name"] for i in o.items)} for o in orders]}


def issue_refund(ctx: ToolContext, order_id: str, amount_gbp: float, reason: str, email: str | None = None) -> dict:
    order = _get_order(ctx, order_id, email)
    amount = round(float(amount_gbp), 2)
    if amount <= 0:
        raise ToolError("Refund amount must be positive.")
    if order.status == "cancelled":
        raise ToolError(f"Order {order.id} was cancelled and has already been refunded in full.")
    if order.status == "processing":
        raise ToolError(f"Order {order.id} has not been dispatched yet - cancel it instead of refunding.")
    # Idempotency: a retried job / repeated model call must not refund twice
    recent = ctx.db.scalar(select(Refund).where(Refund.order_id == order.id, Refund.amount_gbp == amount,
                                                Refund.created_at >= utcnow() - timedelta(minutes=15)))
    if recent:
        return {"refund_id": recent.id, "status": recent.status, "amount_gbp": amount, "duplicate": True,
                "note": "This refund was already processed a moment ago - not issued twice."}
    remaining = round(order.total_gbp - order.refunded_gbp, 2)
    if amount > remaining:
        raise ToolError(f"Amount exceeds the refundable balance of £{remaining:.2f}.")
    if amount > settings.refund_auto_limit_gbp:
        r = Refund(order_id=order.id, amount_gbp=amount, reason=reason, status="pending_approval", issued_by=ctx.actor)
        ctx.db.add(r)
        ctx.db.flush()
        _audit(ctx, "refund", str(r.id), "refund_requested", {"order_id": order.id, "amount_gbp": amount, "reason": reason})
        return {"refund_id": r.id, "status": "pending_approval", "amount_gbp": amount,
                "note": f"Refunds over £{settings.refund_auto_limit_gbp:.0f} need team-leader approval (within 2 working days)."}
    r = Refund(order_id=order.id, amount_gbp=amount, reason=reason, status="issued", issued_by=ctx.actor)
    ctx.db.add(r)
    order.refunded_gbp = round(order.refunded_gbp + amount, 2)
    order.status = "refunded" if order.refunded_gbp >= order.total_gbp - 0.01 else "partially_refunded"
    ctx.db.flush()
    _audit(ctx, "order", order.id, "refund_issued", {"refund_id": r.id, "amount_gbp": amount, "reason": reason})
    return {"refund_id": r.id, "status": "issued", "amount_gbp": amount, "order_status": order.status,
            "note": "Card refunds appear in 5-7 working days; PayPal within 24 hours."}


def cancel_order(ctx: ToolContext, order_id: str, reason: str, email: str | None = None) -> dict:
    order = _get_order(ctx, order_id, email)
    if order.status != "processing":
        raise ToolError(f"Order {order.id} is '{order.status}' and can no longer be cancelled. Offer a return instead.")
    order.status = "cancelled"
    order.refunded_gbp = order.total_gbp
    ctx.db.add(Refund(order_id=order.id, amount_gbp=order.total_gbp, reason=f"Cancelled: {reason}", issued_by=ctx.actor))
    _audit(ctx, "order", order.id, "order_cancelled", {"reason": reason, "refund_gbp": order.total_gbp})
    return {"order_id": order.id, "status": "cancelled", "refund_gbp": order.total_gbp,
            "note": "Cancelled orders are refunded in full within 3 working days."}


def update_shipping_address(ctx: ToolContext, order_id: str, new_address: str, email: str | None = None) -> dict:
    order = _get_order(ctx, order_id, email)
    if order.status != "processing":
        raise ToolError(f"Order {order.id} has already been dispatched ({order.status}); the address can't be changed. "
                        f"The customer can redirect via the {order.carrier or 'carrier'} tracking link.")
    if len(new_address.strip()) < 10 or not re.search(r"[A-Z]{1,2}\d", new_address.upper()):
        raise ToolError("That doesn't look like a full UK address with postcode. Ask the customer to confirm it.")
    old = order.shipping_address
    order.shipping_address = new_address.strip()
    _audit(ctx, "order", order.id, "address_updated", {"old": old, "new": order.shipping_address})
    return {"order_id": order.id, "shipping_address": order.shipping_address, "status": order.status}


def add_customer_note(ctx: ToolContext, note: str) -> dict:
    if ctx.customer_id is None:
        raise ToolError("No identified customer to attach a note to.")
    c = ctx.db.get(Customer, ctx.customer_id)
    stamp = utcnow().strftime("%Y-%m-%d")
    c.notes = (c.notes + f"\n[{stamp}] {note.strip()}").strip()[-2000:]
    _audit(ctx, "customer", str(c.id), "note_added", {"note": note})
    return {"ok": True}


TOOLS: dict[str, tuple[Callable[..., dict], str, dict]] = {
    "get_order_status": (get_order_status, "Look up an order's status, items, tracking and delivery estimate.", {
        "order_id": {"type": "string", "description": "Order number, e.g. UK-10002"},
        "email": {"type": "string", "description": "Account email - only needed if the customer is not identified"}}),
    "list_customer_orders": (list_customer_orders, "List the identified customer's recent orders.", {
        "email": {"type": "string", "description": "Account email if customer not identified"}}),
    "issue_refund": (issue_refund, "Issue a (partial) refund to the original payment method. Refunds over the auto-approval limit are queued for a team leader.", {
        "order_id": {"type": "string"}, "amount_gbp": {"type": "number", "description": "Amount in GBP"},
        "reason": {"type": "string"}, "email": {"type": "string"}}),
    "cancel_order": (cancel_order, "Cancel an order that has not been dispatched yet and refund it in full.", {
        "order_id": {"type": "string"}, "reason": {"type": "string"}, "email": {"type": "string"}}),
    "update_shipping_address": (update_shipping_address, "Change the delivery address of an order that is still processing.", {
        "order_id": {"type": "string"}, "new_address": {"type": "string", "description": "Full UK address with postcode"},
        "email": {"type": "string"}}),
    "add_customer_note": (add_customer_note, "Save a durable note on the customer's record (preferences, promises made).", {
        "note": {"type": "string"}}),
}

REQUIRED = {"get_order_status": ["order_id"], "list_customer_orders": [], "issue_refund": ["order_id", "amount_gbp", "reason"],
            "cancel_order": ["order_id", "reason"], "update_shipping_address": ["order_id", "new_address"],
            "add_customer_note": ["note"]}


def tool_schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": name, "description": desc,
             "parameters": {"type": "object", "properties": props, "required": REQUIRED[name]}}}
            for name, (_, desc, props) in TOOLS.items()]


def run_tool(ctx: ToolContext, name: str, arguments: str | dict) -> dict[str, Any]:
    """Execute a tool call; never raises - errors are returned to the model so it can recover."""
    if name not in TOOLS:
        return {"error": f"Unknown tool {name}"}
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError:
        return {"error": "Arguments were not valid JSON"}
    fn = TOOLS[name][0]
    allowed = set(TOOLS[name][2])
    args = {k: v for k, v in args.items() if k in allowed and v not in (None, "")}
    try:
        with ctx.db.begin_nested():  # savepoint: a failed tool never leaves partial writes
            return fn(ctx, **args)
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"Bad arguments: {e}"}
