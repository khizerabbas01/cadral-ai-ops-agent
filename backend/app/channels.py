"""Inbound channels: web chat (API), email (IMAP polling or inbound-parse webhook) and Telegram
(webhook or long polling). All of them call orchestrator.ingest(), which persists then queues."""
from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from email.header import decode_header, make_header
from email.utils import parseaddr

import httpx

from .config import settings
from .db import SessionLocal

log = logging.getLogger("channels")


def _strip_quoted(body: str) -> str:
    """Remove quoted history from email replies so only the new text is processed."""
    lines = []
    for line in body.splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:$", line.strip()) or line.strip() == "--":
            break
        lines.append(line)
    return "\n".join(lines).strip() or body.strip()


def handle_email(from_header: str, subject: str, body: str, message_id: str | None = None,
                 in_reply_to: str | None = None) -> int:
    from .orchestrator import ingest
    name, addr = parseaddr(from_header)
    thread_ref = in_reply_to or message_id or f"{addr}:{subject}"
    db = SessionLocal()
    try:
        conv, _ = ingest(db, channel="email", text=_strip_quoted(body), email=addr, name=name or None,
                         external_ref=thread_ref, subject=re.sub(r"^(re|fwd?):\s*", "", subject or "", flags=re.I))
        if message_id and not conv.external_ref:
            conv.external_ref = message_id
            db.commit()
        return conv.id
    finally:
        db.close()


def _decode(value: str | None) -> str:
    return str(make_header(decode_header(value))) if value else ""


def poll_imap() -> int:
    """Fetch UNSEEN emails from the support inbox (Gmail with an app password works on the free tier)."""
    if not (settings.imap_host and settings.imap_user):
        return 0
    n = 0
    try:
        with imaplib.IMAP4_SSL(settings.imap_host) as box:
            box.login(settings.imap_user, settings.imap_password)
            box.select("INBOX")
            _, ids = box.search(None, "UNSEEN")
            for num in ids[0].split()[:25]:
                _, data = box.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                if msg.get("Auto-Submitted", "no") != "no" or "noreply" in (msg.get("From") or "").lower():
                    continue
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                            body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                            break
                else:
                    body = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace")
                handle_email(_decode(msg.get("From")), _decode(msg.get("Subject")), body,
                             msg.get("Message-ID"), msg.get("In-Reply-To"))
                n += 1  # message is now durable in our DB; IMAP marks it \Seen on fetch
    except Exception:
        log.exception("IMAP poll failed - will retry next interval")
    return n


def handle_telegram_update(update: dict) -> int | None:
    from .orchestrator import ingest
    msg = update.get("message") or update.get("edited_message")
    if not msg or not msg.get("text"):
        return None
    chat_id = str(msg["chat"]["id"])
    text = msg["text"]
    if text.strip() == "/start":
        from . import notify
        try:
            notify.telegram_send(chat_id, "Hi! I'm the Harbour & Lane assistant. Ask me about delivery, returns or your order "
                                          "(include your order number, e.g. UK-10002, and your account email).")
        except Exception:
            log.exception("telegram /start reply failed")
        return None
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    sender = msg.get("from", {})
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or "Telegram user"
    db = SessionLocal()
    try:
        conv, _ = ingest(db, channel="telegram", text=text, telegram_chat_id=chat_id, name=name,
                         email=email_match.group(0) if email_match else None, external_ref=f"tg:{chat_id}")
        return conv.id
    finally:
        db.close()


def telegram_poll_loop() -> None:
    """Long polling - handy for local dev where Telegram can't reach a webhook."""
    offset = 0
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    try:
        httpx.post(f"{base}/deleteWebhook", timeout=10)
    except Exception:
        pass
    while True:
        try:
            r = httpx.get(f"{base}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40)
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                handle_telegram_update(upd)
        except Exception:
            log.exception("telegram polling error")
            time.sleep(5)
