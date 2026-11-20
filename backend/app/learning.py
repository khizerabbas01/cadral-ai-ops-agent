"""Self-improving loop.

1. Human edits/rejections on the dashboard are stored as Feedback rows.
2. Edited & approved replies become *few-shot examples*: at answer time we retrieve the most similar past
   corrections (semantic search over the customer message) and inject them into the agent prompt.
3. Rejections (and big edits) are distilled by the LLM into one-line *prompt rules* that are appended to the
   relevant agent's system prompt (admins can toggle them off on the dashboard).
4. Everything can be exported as a chat-format JSONL re-training dataset for the next fine-tune.
"""
from __future__ import annotations

import difflib
import json
import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import llm
from .config import settings
from .models import Feedback, PromptRule
from .rag import kb, tokenize

log = logging.getLogger("learning")


def active_rules(db: Session, agent: str) -> list[str]:
    rows = db.scalars(select(PromptRule).where(PromptRule.active.is_(True), PromptRule.agent.in_([agent, "all"]))
                      .order_by(PromptRule.id.desc()).limit(15)).all()
    return [r.rule for r in reversed(rows)]


def similar_examples(db: Session, agent: str, customer_message: str, k: int = 3, min_sim: float = 0.55) -> list[dict]:
    rows = db.scalars(select(Feedback).where(Feedback.action.in_(["edit", "approve"]), Feedback.agent == agent,
                                             Feedback.final_reply.is_not(None))
                      .order_by(Feedback.id.desc()).limit(200)).all()
    rows = [r for r in rows if r.customer_message]
    if not rows:
        return []
    # Edits are the most valuable signal; approvals only count when there is no edit for that message
    try:
        if kb.embedder is None:
            kb.build()
        vecs = kb.embedder.embed([r.customer_message for r in rows])
        q = kb.embedder.embed([customer_message], is_query=True)[0]
        sims = vecs @ q
    except Exception:
        qs = set(tokenize(customer_message))
        sims = np.array([len(qs & set(tokenize(r.customer_message))) / max(1, len(qs)) for r in rows])
    order = np.argsort(-sims)
    out = []
    for i in order[:k]:
        if sims[i] < min_sim:
            break
        r = rows[i]
        out.append({"customer": r.customer_message, "reply": r.final_reply, "edited": r.action == "edit",
                    "similarity": round(float(sims[i]), 3)})
    return out


def format_examples(examples: list[dict]) -> str:
    if not examples:
        return ""
    parts = ["Replies previously written or approved by our human team for similar messages "
             "(match their tone and level of detail; facts must still come from tools/sources):"]
    for e in examples:
        parts.append(f"- Customer: {e['customer']}\n  Approved reply: {e['reply']}")
    return "\n".join(parts)


def distil_rule(db: Session, fb: Feedback) -> PromptRule | None:
    """Turn a rejection / substantial edit into a reusable one-line guideline."""
    if fb.action == "approve":
        return None
    if fb.action == "edit" and fb.final_reply:
        ratio = difflib.SequenceMatcher(None, fb.original_reply, fb.final_reply).ratio()
        if ratio > 0.85 and not fb.reason:
            return None  # cosmetic edit - the example is enough
    rule_text = None
    if settings.llm_enabled:
        try:
            data, _ = llm.chat_json([
                {"role": "system", "content": "You maintain the style guide for an e-commerce support AI. Given a reply "
                 "that a human supervisor rejected or corrected, write ONE short, general, reusable instruction "
                 "(max 30 words) that would prevent the mistake next time. Don't mention specific order numbers or names. "
                 'Respond as JSON: {"rule": "..."}'},
                {"role": "user", "content": json.dumps({"customer_message": fb.customer_message,
                                                        "ai_reply": fb.original_reply,
                                                        "human_action": fb.action,
                                                        "human_reply": fb.final_reply,
                                                        "reason": fb.reason})}],
                purpose="learning:distil_rule", model=settings.llm_fast_model, max_tokens=120)
            rule_text = (data.get("rule") or "").strip()
        except Exception:
            log.exception("rule distillation failed")
    if not rule_text and fb.reason:
        rule_text = f"Supervisor guidance: {fb.reason.strip()}"
    if not rule_text:
        return None
    rule = PromptRule(agent=fb.agent or "all", rule=rule_text[:300], source_feedback_id=fb.id)
    db.add(rule)
    return rule


def export_dataset(db: Session) -> list[dict]:
    """Chat-format SFT records built from human-corrected replies (for the next fine-tuning round)."""
    rows = db.scalars(select(Feedback).where(Feedback.action.in_(["edit", "approve"]))).all()
    return [{"messages": [
        {"role": "system", "content": f"You are the {r.agent} agent for Harbour & Lane, a UK homeware retailer."},
        {"role": "user", "content": r.customer_message},
        {"role": "assistant", "content": r.final_reply}],
        "meta": {"feedback_id": r.id, "action": r.action}} for r in rows if r.final_reply and r.customer_message]
