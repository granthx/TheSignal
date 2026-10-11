"""Email delivery. Real SMTP when SMTP_HOST is set; otherwise every message is written as an .eml file
into the outbox directory so the whole newsletter flow is testable without any mail server."""
from __future__ import annotations

import itertools
import logging
import re
import smtplib
import ssl
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Iterator

from .config import Settings

log = logging.getLogger("signal.mailer")


class MailError(Exception):
    pass


def build_message(
    settings: Settings, to_email: str, subject: str, html: str, text: str, unsubscribe_url: str | None = None
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.mail_from_name, settings.from_address))
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=settings.from_address.rsplit("@", 1)[-1] or "localhost")
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


_outbox_counter = itertools.count(1)  # process-wide, so two sessions in the same second never collide


class _OutboxSession:
    def __init__(self, settings: Settings):
        self.dir = settings.outbox_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, msg: EmailMessage) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(msg["To"]))[:60]
        path = self.dir / f"{stamp}-{next(_outbox_counter):05d}-{safe}.eml"
        path.write_bytes(msg.as_bytes())
        log.info("outbox: wrote %s", path.name)
        return "outbox"


class _SmtpSession:
    def __init__(self, conn: smtplib.SMTP):
        self.conn = conn

    def send(self, msg: EmailMessage) -> str:
        try:
            self.conn.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"{type(exc).__name__}: {exc}") from exc
        return "sent"


class Mailer:
    def __init__(self, settings: Settings):
        self.s = settings

    def _connect(self) -> smtplib.SMTP:
        s = self.s
        try:
            if s.smtp_security == "ssl":
                conn: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30, context=ssl.create_default_context())
            else:
                conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
                conn.ehlo()
                if s.smtp_security == "starttls":
                    conn.starttls(context=ssl.create_default_context())
                    conn.ehlo()
            if s.smtp_user:
                conn.login(s.smtp_user, s.smtp_password)
            return conn
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"could not connect to SMTP server: {type(exc).__name__}: {exc}") from exc

    @contextmanager
    def session(self) -> Iterator[_OutboxSession | _SmtpSession]:
        """One connection reused for a whole batch of messages."""
        if not self.s.smtp_enabled:
            yield _OutboxSession(self.s)
            return
        conn = self._connect()
        try:
            yield _SmtpSession(conn)
        finally:
            try:
                conn.quit()
            except (smtplib.SMTPException, OSError):
                pass

    def send_one(self, msg: EmailMessage) -> str:
        with self.session() as session:
            return session.send(msg)
