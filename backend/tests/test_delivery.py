"""The production delivery path: a real SMTP conversation against a throwaway local server, and the
scheduler's catch-up / grace-window behaviour."""
import asyncio
import email
import email.policy
import socketserver
import threading
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.mailer import MailError
from app.scheduler import Scheduler

from .helpers import make_services, make_settings
from pathlib import Path
import tempfile


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        server = self.server
        self.reply("220 test ESMTP")
        mail_from, rcpts = None, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode().strip()
            up = cmd.upper()
            if up.startswith(("EHLO", "HELO")):
                self.reply("250 test")
            elif up.startswith("MAIL FROM"):
                mail_from = cmd
                self.reply("250 ok")
            elif up.startswith("RCPT TO"):
                if server.reject and server.reject in cmd:
                    self.reply("550 no such user")
                else:
                    rcpts.append(cmd)
                    self.reply("250 ok")
            elif up == "DATA":
                self.reply("354 go")
                data = []
                while True:
                    row = self.rfile.readline()
                    if row in (b".\r\n", b""):
                        break
                    data.append(row[1:] if row.startswith(b"..") else row)
                server.messages.append((mail_from, list(rcpts), b"".join(data)))
                rcpts.clear()
                self.reply("250 queued")
            elif up == "RSET":
                rcpts.clear()
                self.reply("250 ok")
            elif up == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("502 not implemented")


class FakeSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, reject=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.messages, self.reject = [], reject
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.server_address[1]


class SmtpDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.smtp = FakeSMTP()
        self.addCleanup(self.smtp.server_close)
        self.addCleanup(self.smtp.shutdown)

    def services(self, **kw):
        return make_services(smtp_host="127.0.0.1", smtp_port=self.smtp.port, smtp_security="none",
                             mail_from="paper@signal.test", mail_from_name="The Signal", send_welcome_digest=False, **kw)

    def test_confirmation_and_daily_digest_over_real_smtp(self):
        services, settings = self.services()
        services.newsletter.subscribe("ada@example.com", "Ada")
        self.assertEqual(len(self.smtp.messages), 1)
        sender, rcpts, raw = self.smtp.messages[0]
        self.assertIn("paper@signal.test", sender)
        self.assertIn("ada@example.com", rcpts[0])
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        self.assertIn("Confirm", msg["Subject"])
        token = services.db.get_subscriber_by_email("ada@example.com")["token"]
        self.assertIn(token, msg.get_body(preferencelist=("plain",)).get_content())

        services.newsletter.confirm(token)
        for i in range(4):
            services.newsletter.subscribe(f"u{i}@example.com")
            services.newsletter.confirm(services.db.get_subscriber_by_email(f"u{i}@example.com")["token"])
        before = len(self.smtp.messages)
        counts = services.send_daily_job()
        self.assertEqual(counts, {"sent": 5, "skipped": 0, "failed": 0})
        self.assertEqual(len(self.smtp.messages) - before, 5)
        digest = email.message_from_bytes(self.smtp.messages[-1][2], policy=email.policy.default)
        self.assertEqual(digest["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertTrue(digest.is_multipart())
        html = digest.get_body(preferencelist=("html",)).get_content()
        self.assertIn("THE SIGNAL", html)
        self.assertEqual(services.send_daily_job()["sent"], 0, "second run must not re-send")
        self.assertEqual(len(self.smtp.messages) - before, 5)

    def test_one_rejected_recipient_does_not_stop_the_others(self):
        services, settings = self.services()
        for who in ("good1", "bad", "good2"):
            services.newsletter.subscribe(f"{who}@example.com")
            token = services.db.get_subscriber_by_email(f"{who}@example.com")["token"]
            services.newsletter.confirm(token)
        self.smtp.reject = "bad@example.com"  # mailbox disappears after they subscribed
        counts = services.send_daily_job()
        self.assertEqual((counts["sent"], counts["failed"]), (2, 1))

    def test_unreachable_server_is_reported_not_crashed(self):
        services, settings = make_services(smtp_host="127.0.0.1", smtp_port=1, smtp_security="none", send_welcome_digest=False)
        with self.assertRaises(MailError):
            services.newsletter.subscribe("x@example.com")
        services.mailer.send_one = lambda msg: "sent"  # server comes back
        self.assertEqual(services.newsletter.subscribe("x@example.com"), "pending",
                         "a failed confirmation must not lock the address behind the resend cooldown")


class SchedulerTests(unittest.TestCase):
    def run_scheduler(self, send_time, **kw):
        settings = make_settings(Path(tempfile.mkdtemp()), send_time=send_time, **kw)
        calls = {"send": 0, "refresh": 0}
        sched = Scheduler(settings, refresh=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
                          send=lambda: calls.__setitem__("send", calls["send"] + 1), tick=0.05)

        async def go():
            task = asyncio.create_task(sched.run())
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(go())
        return calls

    def test_catch_up_send_when_restarted_inside_the_window(self):
        now = datetime.now(ZoneInfo("UTC"))
        if now.hour == 0 and now.minute < 5:
            self.skipTest("too close to midnight for a same-day window check")
        at = (now - timedelta(minutes=2)).strftime("%H:%M")
        self.assertEqual(self.run_scheduler(at)["send"], 1)

    def test_no_late_send_outside_the_grace_window(self):
        now = datetime.now(ZoneInfo("UTC"))
        if now.hour < 8:
            self.skipTest("needs a send time >6h earlier on the same day")
        at = (now - timedelta(hours=7)).strftime("%H:%M")
        self.assertEqual(self.run_scheduler(at, send_grace_hours=6)["send"], 0)

    def test_nothing_runs_before_send_time(self):
        now = datetime.now(ZoneInfo("UTC"))
        if now.hour >= 23 and now.minute > 50:
            self.skipTest("too close to midnight")
        at = (now + timedelta(minutes=30)).strftime("%H:%M")
        self.assertEqual(self.run_scheduler(at), {"send": 0, "refresh": 0})


if __name__ == "__main__":
    unittest.main()
