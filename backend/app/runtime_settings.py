"""Settings an admin can change at runtime from the dashboard.

They are stored in the database and applied on top of the environment defaults (config.py) at startup and
on every change, so behaviour such as "hold every reply for review" can be switched without a redeploy.
Secrets are never editable here - those stay in environment variables.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import session_scope
from .models import AppSetting


def _reply_mode(value) -> str:
    value = str(value).strip().lower()
    if value not in {"auto", "review"}:
        raise ValueError("reply_mode must be 'auto' or 'review'")
    return value


def _refund_limit(value) -> float:
    value = float(value)
    if not 0 <= value <= 10000:
        raise ValueError("refund limit must be between 0 and 10000")
    return round(value, 2)


EDITABLE = {"reply_mode": _reply_mode, "refund_auto_limit_gbp": _refund_limit}


def load() -> None:
    """Apply stored overrides to the in-memory settings (called once at startup)."""
    with session_scope() as s:
        for row in s.scalars(select(AppSetting)):
            coerce = EDITABLE.get(row.key)
            if coerce is None:
                continue
            try:
                setattr(settings, row.key, coerce(row.value))
            except ValueError:
                pass  # a bad stored value never blocks startup - the env default stays


def update(db: Session, changes: dict) -> dict:
    """Validate, persist and apply. Raises ValueError on an invalid value (nothing is saved)."""
    applied = {key: EDITABLE[key](raw) for key, raw in changes.items() if key in EDITABLE and raw is not None}
    for key, value in applied.items():
        row = db.get(AppSetting, key)
        if row:
            row.value = str(value)
        else:
            db.add(AppSetting(key=key, value=str(value)))
    db.commit()
    for key, value in applied.items():
        setattr(settings, key, value)
    return applied


def get_flag(db: Session, key: str) -> str | None:
    row = db.get(AppSetting, key)
    return row.value if row else None


def set_flag(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
