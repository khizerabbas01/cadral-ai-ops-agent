from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_url = settings.database_url
if _url.startswith("postgres://"):  # Render / Heroku style URLs
    _url = _url.replace("postgres://", "postgresql://", 1)

_connect_args = {}
if _url.startswith("sqlite"):
    Path(_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    _connect_args = {"check_same_thread": False, "timeout": 30}

engine = create_engine(_url, connect_args=_connect_args, pool_pre_ping=True)

if _url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # WAL = concurrent readers while the worker writes
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
