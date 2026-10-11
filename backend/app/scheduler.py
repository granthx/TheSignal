"""A tiny asyncio scheduler (no APScheduler needed): one daily send time plus a periodic refresh.

It wakes every ``tick`` seconds and compares wall-clock time, so it survives a laptop going to sleep, and a
restart after the send time still sends within the grace window. Double sends are impossible because
the send job consults the send log."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .config import Settings

log = logging.getLogger("signal.scheduler")


def parse_hhmm(value: str) -> time:
    try:
        hour, minute = value.strip().split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"SEND_TIME must look like 07:30, got {value!r}") from exc


def next_daily(now: datetime, at: time) -> datetime:
    """The next occurrence of wall-clock time ``at`` strictly after ``now`` (DST-safe)."""
    candidate = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
    if candidate <= now:
        candidate = datetime.combine(now.date() + timedelta(days=1), at, tzinfo=now.tzinfo)
    return candidate


class Scheduler:
    def __init__(self, settings: Settings, refresh: Callable[[], object], send: Callable[[], object], tick: float = 30.0):
        self.s = settings
        self.refresh = refresh
        self.send = send
        self.tick = tick
        self.tz = ZoneInfo(settings.timezone)
        self.at = parse_hhmm(settings.send_time)

    async def _run(self, fn: Callable[[], object], label: str) -> None:
        try:
            await asyncio.to_thread(fn)
        except Exception:  # noqa: BLE001 - a failing job must not kill the scheduler
            log.exception("scheduled job %r crashed", label)

    async def run(self) -> None:
        grace = timedelta(hours=self.s.send_grace_hours)
        now = datetime.now(self.tz)
        today_send = datetime.combine(now.date(), self.at, tzinfo=self.tz)
        next_send = next_daily(now, self.at)
        next_refresh = now + timedelta(hours=self.s.refresh_hours)
        log.info("scheduler up: daily send at %s %s, refresh every %sh", self.s.send_time, self.s.timezone, self.s.refresh_hours)

        if today_send <= now <= today_send + grace:  # restarted after the send time today: catch up
            log.info("inside today's send window; running catch-up send")
            await self._run(self.send, "catch-up send")

        while True:
            await asyncio.sleep(self.tick)
            now = datetime.now(self.tz)
            if now >= next_send:
                scheduled, next_send = next_send, next_daily(now, self.at)
                if now - scheduled <= grace:
                    await self._run(self.send, "daily send")
                else:
                    log.warning("missed the %s send by %s; skipping", scheduled.isoformat(timespec="minutes"), now - scheduled)
            if now >= next_refresh:
                next_refresh = now + timedelta(hours=self.s.refresh_hours)
                await self._run(self.refresh, "refresh")
