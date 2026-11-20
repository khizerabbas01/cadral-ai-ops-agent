"""Durable job queue (DB-backed outbox) + worker threads + scheduler.

Guarantees "nothing is lost if an API fails":
  * inbound messages are committed to the DB *before* any external call; processing happens in a job
  * every external call (LLM, Slack, SMTP, Telegram) runs inside a job with exponential backoff retries
  * jobs that exhaust retries go to a dead-letter state, raise a Slack alert and can be replayed from the dashboard
  * on boot, jobs left 'running' by a crash are put back to 'pending'
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import Job, utcnow

log = logging.getLogger("jobs")

_wake = threading.Event()
_stop = threading.Event()
_threads: list[threading.Thread] = []


# Job kinds that are dropped instead of queued. Offline tools (the sample-traffic loader) set this so that
# replayed sample messages can never send a real email, Telegram message or Slack alert.
SUPPRESSED_KINDS: set[str] = set()


def enqueue(db: Session, kind: str, payload: dict, *, max_attempts: int = 5, delay_s: float = 0) -> Job:
    job = Job(kind=kind, payload=payload, max_attempts=max_attempts, next_run_at=utcnow() + timedelta(seconds=delay_s))
    if kind in SUPPRESSED_KINDS:
        return job  # never persisted, so no worker can pick it up
    db.add(job)
    db.flush()
    _wake.set()
    return job


def backoff_seconds(attempt: int) -> float:
    return min(600.0, 5 * (2 ** (attempt - 1)))  # 5s, 10s, 20s, 40s ... capped at 10 min


def _handlers():
    from . import notify, reporting
    from .orchestrator import deliver_reply, process_inbound

    def slack_alert(p, job):
        if p.get("kind") == "escalation":
            e = p["escalation"]
            url = f"{settings.public_base_url}/#/conversations/{p.get('conversation_id')}"
            notify.slack(f"Escalation ({e['severity']}): {e['customer']} - {e['reason']}", notify.escalation_blocks(e, url))
        else:
            notify.slack(p.get("text", "System alert"))

    return {
        "process_inbound": lambda p, job: process_inbound(p["message_id"], final_attempt=job.attempts >= job.max_attempts),
        "deliver_reply": lambda p, job: deliver_reply(p["message_id"]),
        "slack_alert": slack_alert,
        "send_email": lambda p, job: notify.send_email(p["to"], p["subject"], p["body"], p.get("html")),
        "daily_report": lambda p, job: reporting.send_daily_report(),
        "distil_rule": _distil_rule,
    }


def _distil_rule(p, job):
    from . import learning
    from .models import Feedback
    db = SessionLocal()
    try:
        fb = db.get(Feedback, p["feedback_id"])
        if fb and learning.distil_rule(db, fb):
            db.commit()
    finally:
        db.close()


def _claim(db: Session) -> Job | None:
    job = db.scalar(select(Job).where(Job.status == "pending", Job.next_run_at <= utcnow()).order_by(Job.next_run_at).limit(1))
    if job is None:
        return None
    # optimistic lock: only one worker wins the row
    res = db.execute(update(Job).where(Job.id == job.id, Job.status == "pending")
                     .values(status="running", attempts=Job.attempts + 1, updated_at=utcnow()))
    db.commit()
    if res.rowcount != 1:
        return None
    db.refresh(job)
    return job


def run_one(handlers=None) -> bool:
    from .notify import NotConfigured
    handlers = handlers or _handlers()
    db = SessionLocal()
    try:
        job = _claim(db)
        if job is None:
            return False
        try:
            handlers[job.kind](job.payload, job)
            job.status, job.last_error = "done", None
        except NotConfigured as e:
            job.status, job.last_error = "done", f"skipped: {e}"
            log.info("job %s %s skipped: %s", job.id, job.kind, e)
        except Exception as e:  # retry with backoff, then dead-letter
            job.last_error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
            if job.attempts >= job.max_attempts:
                job.status = "dead"
                log.error("job %s %s dead after %s attempts: %s", job.id, job.kind, job.attempts, e)
                if job.kind != "slack_alert":
                    enqueue(db, "slack_alert", {"kind": "error", "text": f":x: Job #{job.id} ({job.kind}) failed permanently: {str(e)[:300]}"})
            else:
                job.status = "pending"
                job.next_run_at = utcnow() + timedelta(seconds=backoff_seconds(job.attempts))
                log.warning("job %s %s failed (attempt %s), retrying: %s", job.id, job.kind, job.attempts, e)
        job.updated_at = utcnow()
        db.commit()
        return True
    finally:
        db.close()


def drain(max_jobs: int = 1000) -> int:
    """Run all due jobs synchronously (used by tests / evals)."""
    handlers, n = _handlers(), 0
    while n < max_jobs and run_one(handlers):
        n += 1
    return n


def _worker_loop():
    handlers = _handlers()
    while not _stop.is_set():
        try:
            if not run_one(handlers):
                _wake.wait(1.0)
                _wake.clear()
        except Exception:
            log.exception("worker loop error")
            time.sleep(2)


def recover_stuck_jobs() -> None:
    db = SessionLocal()
    try:
        db.execute(update(Job).where(Job.status == "running").values(status="pending"))
        db.commit()
    finally:
        db.close()


def start_workers(n: int = 3) -> None:
    recover_stuck_jobs()
    for i in range(n):
        t = threading.Thread(target=_worker_loop, name=f"job-worker-{i}", daemon=True)
        t.start()
        _threads.append(t)


def stop_workers() -> None:
    _stop.set()
    _wake.set()


# ------------------------------------------------------------------ scheduler
_scheduler = None


def start_scheduler() -> None:
    global _scheduler
    if not settings.enable_scheduler:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    from . import channels

    def queue_report():
        db = SessionLocal()
        try:
            enqueue(db, "daily_report", {}, max_attempts=6)
            db.commit()
        finally:
            db.close()

    _scheduler = BackgroundScheduler(timezone="Europe/London")
    _scheduler.add_job(queue_report, "cron", hour=settings.report_cron_hour, minute=0, id="daily_report")
    if settings.imap_host:
        _scheduler.add_job(channels.poll_imap, "interval", seconds=settings.imap_poll_seconds, id="imap_poll",
                           max_instances=1, coalesce=True)
    _scheduler.start()
    if settings.telegram_bot_token and settings.telegram_polling:
        threading.Thread(target=channels.telegram_poll_loop, name="telegram-poll", daemon=True).start()
    log.info("scheduler started (daily report %02d:00 Europe/London)", settings.report_cron_hour)


def stop_scheduler() -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)


def next_report_time() -> str | None:
    """ISO time of the next scheduled daily report (None when the scheduler is off)."""
    try:
        job = _scheduler.get_job("daily_report") if _scheduler else None
        return job.next_run_time.isoformat() if job and job.next_run_time else None
    except Exception:
        return None
