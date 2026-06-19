"""Cred-optional alert engine.

Two channels:
  * SMTP (Gmail-friendly: SSL on 465 by default; H10 falls back to STARTTLS
    on 587 if 465 is unavailable / authentication fails)
  * Slack via incoming-webhook POST

If neither channel has creds set, the engine logs to stdout/file at INFO
level and returns success — the auto_trader build never blocks on alert
config (per the user's chosen scope).

Alert types are thresholded per the ``ALERT_ON_*`` config flags.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from auto_trader.config import (
    ALERT_ON_BEAR_REGIME,
    ALERT_ON_DRAWDOWN_HALT,
    ALERT_ON_MONTHLY_COMPLETE,
    ALERT_ON_SCORE_DECAY,
    ALERT_ON_SIGNAL_EXIT,
    ALERT_ON_STOP_LOSS,
)

logger = logging.getLogger(__name__)


_TYPE_FLAGS = {
    "STOP_LOSS": ALERT_ON_STOP_LOSS,
    "SIGNAL_EXIT": ALERT_ON_SIGNAL_EXIT,
    "SCORE_DECAY": ALERT_ON_SCORE_DECAY,
    "DRAWDOWN_HALT": ALERT_ON_DRAWDOWN_HALT,
    "MONTHLY_COMPLETE": ALERT_ON_MONTHLY_COMPLETE,
    "BEAR_REGIME": ALERT_ON_BEAR_REGIME,
}


def send_alert(
    subject: str,
    body: str,
    alert_type: str = "OTHER",
    force: bool = False,
) -> bool:
    """Send an alert through the available channels.

    Args:
        subject: short notification subject line
        body: longer message body (plain text)
        alert_type: one of ``_TYPE_FLAGS`` keys (controls per-type silencing)
        force: bypass the per-type ``ALERT_ON_*`` flags

    Returns True if at least one channel succeeded (or fell back to log).
    """
    if not force and not _TYPE_FLAGS.get(alert_type, True):
        logger.debug("Alert type %s muted by config", alert_type)
        return True

    sent_any = False
    sent_any |= _try_smtp(subject, body)
    sent_any |= _try_slack(subject, body)

    if not sent_any:
        # Fallback: log to stdout/file. The build is operational without
        # any alert creds.
        logger.info("[ALERT/%s] %s | %s", alert_type, subject, body[:300])
        sent_any = True
    return sent_any


def _try_smtp(subject: str, body: str) -> bool:
    """Send via SMTP. Returns True on success, False on absent creds / errors."""
    password = os.getenv("SMTP_PASSWORD", "").strip()
    sender = os.getenv("ALERT_EMAIL_FROM", "").strip()
    recipient = os.getenv("ALERT_EMAIL_TO", sender).strip()
    if not (password and sender and recipient):
        return False

    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    use_ssl = os.getenv("SMTP_USE_SSL", "true").strip().lower() == "true"
    try:
        port = int(os.getenv("SMTP_PORT", "465" if use_ssl else "587"))
    except ValueError:
        port = 465 if use_ssl else 587

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"[auto_trader] {subject}"
    msg.set_content(body)

    # H10: try the configured protocol first, then fall back to the other
    # one if it fails — covers the Gmail SSL-on-465 vs STARTTLS-on-587 swap.
    primary, fallback = (
        ("ssl", "starttls") if use_ssl else ("starttls", "ssl")
    )
    for protocol in (primary, fallback):
        try:
            if protocol == "ssl":
                with smtplib.SMTP_SSL(host, 465, timeout=10) as s:
                    s.login(sender, password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, 587, timeout=10) as s:
                    s.starttls()
                    s.login(sender, password)
                    s.send_message(msg)
            logger.info("Alert sent via SMTP (%s, port=%d)",
                        protocol, 465 if protocol == "ssl" else 587)
            return True
        except Exception as exc:
            logger.warning("SMTP %s failed: %s", protocol, exc)
            continue
    return False


def _try_slack(subject: str, body: str) -> bool:
    """POST to a Slack incoming-webhook. Returns True on success."""
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return False
    try:
        import requests

        resp = requests.post(
            webhook,
            json={"text": f"*{subject}*\n```\n{body}\n```"},
            timeout=10,
        )
        if resp.status_code in (200, 204):
            logger.info("Alert sent via Slack")
            return True
        logger.warning("Slack returned status %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Slack post failed: %s", exc)
    return False


__all__ = ["send_alert"]
