"""Newsletter service: subscribe (double opt-in), confirm, unsubscribe, and the daily send."""
from __future__ import annotations

import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .db import Database
from .digest import build_digest, render_confirmation
from .mailer import MailError, Mailer, build_message
from .pipeline import local_today

log = logging.getLogger("signal.newsletter")

_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f<>]")
RESEND_COOLDOWN_SECONDS = 600


def normalize_email(raw: Any) -> str:
    email = str(raw or "").strip().lower()
    if not email or len(email) > 254 or not _EMAIL.match(email) or ".." in email:
        raise ValueError("That doesn't look like a valid email address.")
    return email


def clean_name(raw: Any) -> str:
    return _CONTROL.sub("", str(raw or "")).strip()[:60]


def _seconds_since(iso: str) -> float:
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds()


class NewsletterService:
    def __init__(self, settings: Settings, db: Database, mailer: Mailer):
        self.s = settings
        self.db = db
        self.mailer = mailer

    # ------------------------------------------------------------------ links
    def confirm_url(self, token: str) -> str:
        return f"{self.s.base_url}/api/confirm?token={token}"

    def unsubscribe_url(self, token: str) -> str:
        return f"{self.s.base_url}/api/unsubscribe?token={token}"

    # ------------------------------------------------------------------ subscribe flow
    def subscribe(self, email_raw: Any, name_raw: Any = "") -> str:
        """Returns 'pending' (confirmation mail sent), 'confirmed' (no double opt-in) or 'noop'.

        The HTTP layer deliberately answers all three identically so the form cannot be used to find out
        who is already subscribed. Raises ValueError for a malformed address and MailError if delivery fails."""
        email, name = normalize_email(email_raw), clean_name(name_raw)
        existing = self.db.get_subscriber_by_email(email)

        if existing and existing["status"] == "confirmed":
            return "noop"
        if existing and existing["status"] == "pending" and _seconds_since(existing["updated_at"]) < RESEND_COOLDOWN_SECONDS:
            return "noop"  # a confirmation mail went out moments ago; do not let the form spam an inbox

        token = secrets.token_urlsafe(24)
        status = "pending" if self.s.double_opt_in else "confirmed"
        if existing:
            self.db.reset_subscriber(existing["id"], name, token, status)
            subscriber = self.db.get_subscriber_by_token(token)
        else:
            subscriber = self.db.create_subscriber(email, name, token, status)

        if status == "pending":
            subject, html, text = render_confirmation(self.s, self.confirm_url(token))
            try:
                self.mailer.send_one(build_message(self.s, email, subject, html, text))
            except MailError:
                if not existing:  # let the person retry immediately instead of waiting out the cooldown
                    self.db.set_status(subscriber["id"], "unsubscribed")
                raise
            return "pending"

        self._welcome(subscriber)
        return "confirmed"

    def confirm(self, token: str) -> dict[str, Any]:
        sub = self.db.get_subscriber_by_token(token) if token else None
        if not sub or sub["status"] == "unsubscribed":
            return {"ok": False, "reason": "invalid"}
        if sub["status"] == "pending":
            self.db.set_status(sub["id"], "confirmed")
            sub = {**sub, "status": "confirmed"}
            self._welcome(sub)
        return {"ok": True, "email": sub["email"]}

    def unsubscribe(self, token: str) -> dict[str, Any]:
        sub = self.db.get_subscriber_by_token(token) if token else None
        if not sub:
            return {"ok": False, "reason": "invalid"}
        if sub["status"] != "unsubscribed":
            self.db.set_status(sub["id"], "unsubscribed")
        return {"ok": True}

    def lookup(self, token: str) -> dict[str, Any] | None:
        return self.db.get_subscriber_by_token(token) if token else None

    # ------------------------------------------------------------------ sending
    def _welcome(self, subscriber: dict[str, Any]) -> None:
        """Send today's briefing right after confirmation so the first email arrives immediately."""
        if not self.s.send_welcome_digest:
            return
        edition = self.db.get_edition(local_today(self.s))
        if not edition or edition.get("mode") != "live" or not edition.get("lead"):
            return
        try:
            self.send_to(subscriber, edition)
        except MailError as exc:
            log.warning("welcome digest to subscriber %s failed: %s", subscriber["id"], exc)

    def send_to(self, subscriber: dict[str, Any], edition: dict[str, Any], session=None) -> str:
        digest = build_digest(edition, self.s, self.unsubscribe_url(subscriber["token"]))
        msg = build_message(
            self.s, subscriber["email"], digest.subject, digest.html, digest.text, self.unsubscribe_url(subscriber["token"])
        )
        try:
            status = session.send(msg) if session else self.mailer.send_one(msg)
        except MailError as exc:
            self.db.log_send(subscriber["id"], edition["date"], "failed", str(exc)[:300])
            raise
        self.db.log_send(subscriber["id"], edition["date"], status)
        return status

    def send_daily(self, edition: dict[str, Any]) -> dict[str, int]:
        """Send ``edition`` to every confirmed subscriber who has not had it yet (safe to call twice)."""
        counts = {"sent": 0, "skipped": 0, "failed": 0}
        if not edition.get("lead"):
            log.warning("edition %s has no stories; nothing to send", edition.get("date"))
            return counts
        pending = []
        for sub in self.db.confirmed_subscribers():
            if self.db.was_sent(sub["id"], edition["date"]):
                counts["skipped"] += 1
            else:
                pending.append(sub)
        if not pending:
            return counts
        try:
            with self.mailer.session() as session:
                for sub in pending:
                    try:
                        self.send_to(sub, edition, session)
                        counts["sent"] += 1
                    except MailError as exc:
                        counts["failed"] += 1
                        log.warning("send to subscriber %s failed: %s", sub["id"], exc)
                    time.sleep(self.s.send_delay_seconds)
        except MailError as exc:  # could not even connect
            log.error("SMTP connection failed: %s", exc)
            counts["failed"] += len(pending) - counts["sent"] - counts["failed"]
        log.info("daily send for %s: %s", edition["date"], counts)
        return counts
