"""Outbound integrations: Slack webhook, SMTP email, Telegram Bot API.
These functions raise on failure - callers run them inside retryable jobs (see jobs.py)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import settings

log = logging.getLogger("notify")


class NotConfigured(Exception):
    """Integration not configured - the job is marked done (logged) instead of retried forever."""


def slack(text: str, blocks: list[dict] | None = None) -> None:
    if not settings.slack_webhook_url:
        raise NotConfigured("SLACK_WEBHOOK_URL not set")
    r = httpx.post(settings.slack_webhook_url, json={"text": text, **({"blocks": blocks} if blocks else {})}, timeout=10)
    r.raise_for_status()


def send_email(to: str, subject: str, body: str, html: str | None = None, in_reply_to: str | None = None) -> None:
    if not (settings.smtp_host and settings.smtp_user):
        raise NotConfigured("SMTP not configured")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=20) as s:
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)


def telegram_send(chat_id: str, text: str) -> None:
    if not settings.telegram_bot_token:
        raise NotConfigured("TELEGRAM_BOT_TOKEN not set")
    r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                   json={"chat_id": chat_id, "text": text}, timeout=15)
    r.raise_for_status()


def escalation_blocks(esc: dict, conv_url: str) -> list[dict]:
    emoji = {"critical": ":rotating_light:", "high": ":red_circle:", "medium": ":large_orange_circle:", "low": ":large_blue_circle:"}
    return [
        {"type": "header", "text": {"type": "plain_text", "text": f"Escalation - {esc['severity'].upper()}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{emoji.get(esc['severity'], '')} *{esc['customer']}* via *{esc['channel']}*\n*Reason:* {esc['reason']}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Summary:* {esc['summary']}\n*Suggested:* {esc.get('suggested_action') or '-'}"}},
        {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open conversation"}, "url": conv_url}]},
    ]
