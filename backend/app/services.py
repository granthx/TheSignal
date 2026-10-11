"""Wires the pieces together and owns the jobs the scheduler and the admin API both call."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .db import Database
from .mailer import Mailer
from .newsletter import NewsletterService
from .pipeline import Pipeline, PipelineError, local_today
from .util import parse_iso, utcnow

log = logging.getLogger("signal.services")


class Busy(Exception):
    """A pipeline run is already in progress."""


class Services:
    def __init__(self, settings: Settings, pipeline: Pipeline | None = None):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.db.init()
        self.mailer = Mailer(settings)
        self.pipeline = pipeline or Pipeline(settings, self.db)
        self.newsletter = NewsletterService(settings, self.db, self.mailer)
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.last_run_at: str | None = None

    # ------------------------------------------------------------------ edition
    def refresh(self) -> dict[str, Any]:
        """Run the pipeline now. Raises Busy if one is already running, PipelineError if nothing could be fetched."""
        if not self._lock.acquire(blocking=False):
            raise Busy()
        try:
            edition = self.pipeline.run()
            self.last_error = None
            self.last_run_at = utcnow().isoformat(timespec="seconds")
            return edition
        except PipelineError as exc:
            self.last_error = str(exc)
            raise
        finally:
            self._lock.release()

    def refresh_quietly(self) -> None:
        """Scheduler entry point: never raises."""
        try:
            self.refresh()
        except Busy:
            log.info("refresh skipped: another run is in progress")
        except PipelineError as exc:
            log.error("refresh failed: %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("refresh crashed")

    def todays_edition(self) -> dict[str, Any] | None:
        edition = self.db.get_edition(local_today(self.settings))
        return edition if edition and edition.get("mode") == "live" else None

    def bootstrap(self) -> None:
        """On startup, make sure there is an edition for today."""
        if self.todays_edition() is None:
            log.info("no edition for today yet; building one")
            self.refresh_quietly()

    # ------------------------------------------------------------------ newsletter
    def send_daily_job(self, max_age_minutes: int = 60) -> dict[str, int] | None:
        """Build a fresh edition (unless one is already very recent) and mail it to everyone who has not had it."""
        today = local_today(self.settings)
        waiting = [s for s in self.db.confirmed_subscribers() if not self.db.was_sent(s["id"], today)]
        if not waiting:
            return {"sent": 0, "skipped": 0, "failed": 0}

        edition = self.todays_edition()
        generated = parse_iso(edition["generated_at"]) if edition else None
        stale = generated is None or (datetime.now(timezone.utc) - generated).total_seconds() > max_age_minutes * 60
        if stale:
            try:
                edition = self.refresh()
            except (Busy, PipelineError) as exc:
                log.warning("could not refresh before sending (%s); %s", exc or "busy",
                            "using the edition we already have" if edition else "nothing to send")
        if not edition:
            log.error("daily send aborted: no live edition for %s", today)
            return None
        return self.newsletter.send_daily(edition)

    def send_daily_quietly(self) -> None:
        try:
            self.send_daily_job()
        except Exception:  # noqa: BLE001
            log.exception("daily send crashed")
