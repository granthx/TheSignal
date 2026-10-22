"""HTTP layer (Starlette): JSON API for the frontend, newsletter endpoints, admin API, static frontend."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import sudoku
from .config import Settings
from .demo import demo_edition
from .digest import build_digest, render_page
from .mailer import MailError
from .pipeline import PipelineError, local_today
from .scheduler import Scheduler
from .services import Busy, Services

log = logging.getLogger("signal.api")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUBSCRIBE_OK = "Almost there. If this address isn't already on the list, a confirmation link is on its way."
SUBSCRIBE_OK_NO_CONFIRM = "You're in. The next briefing lands at the usual time."

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
)


class SecurityHeaders:
    """Pure ASGI middleware adding conservative security headers to every response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                headers.setdefault("Content-Security-Policy", CSP)
                headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                headers["Pragma"] = "no-cache"
                headers["Expires"] = "0"
            await send(message)

        await self.app(scope, receive, wrapped)


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit, self.window = limit, window_seconds
        self.hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if len(self.hits) > 5000:
            self.hits.clear()
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


def create_app(settings: Settings | None = None, services: Services | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    services = services or Services(settings)
    limiter = RateLimiter(limit=6, window_seconds=600)

    def run(fn, *args):
        return anyio.to_thread.run_sync(fn, *args)

    def error(message: str, status: int) -> JSONResponse:
        return JSONResponse({"ok": False, "error": message}, status_code=status)

    def client_ip(request: Request) -> str:
        if settings.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def page(title: str, heading: str, message: str, ok: bool = True, action: dict | None = None, status: int = 200):
        return HTMLResponse(render_page(settings, title=title, heading=heading, message=message, ok=ok, action=action), status_code=status)

    # ------------------------------------------------------------------ public JSON
    async def health(request: Request):
        return JSONResponse({"ok": True})

    async def meta(request: Request):
        return JSONResponse(
            {
                "site_name": settings.site_name,
                "send_time": settings.send_time,
                "timezone": settings.timezone,
                "double_opt_in": settings.double_opt_in,
                "llm": settings.llm_enabled,
                "today": local_today(settings),
            }
        )

    async def edition(request: Request):
        date = request.query_params.get("date")
        if date:
            if not DATE_RE.match(date):
                return error("date must look like YYYY-MM-DD", 400)
            ed = await run(services.db.get_edition, date)
            if not ed:
                return error("no edition for that date", 404)
        else:
            ed = await run(services.db.latest_edition) or demo_edition(settings)
        today = local_today(settings)
        return JSONResponse({**ed, "today": today, "is_today": ed.get("date") == today}, headers={"Cache-Control": "no-cache"})

    async def refresh_news(request: Request):
        try:
            ed = await run(services.refresh)
        except Busy:
            return error("A refresh is already running in the background. Please wait a moment.", 409)
        except PipelineError as exc:
            return error(f"Could not refresh: {exc}", 502)
        today = local_today(settings)
        return JSONResponse({**ed, "today": today, "is_today": ed.get("date") == today, "ok": True}, headers={"Cache-Control": "no-cache"})

    async def editions(request: Request):
        return JSONResponse({"editions": await run(services.db.list_editions, 30)}, headers={"Cache-Control": "no-cache"})

    async def sudoku_puzzle(request: Request):
        difficulty = request.query_params.get("difficulty", "medium").lower()
        if difficulty not in sudoku.TARGET_CLUES:
            return error("difficulty must be easy, medium or hard", 400)
        if request.query_params.get("mode") == "random":
            puzzle = await run(sudoku.random_puzzle, difficulty)
        else:
            date = request.query_params.get("date") or local_today(settings)
            if not DATE_RE.match(date):
                return error("date must look like YYYY-MM-DD", 400)
            puzzle = await run(sudoku.daily, date, difficulty)
        return JSONResponse(puzzle, headers={"Cache-Control": "no-store"})

    async def email_preview(request: Request):
        ed = await run(services.db.latest_edition) or demo_edition(settings)
        try:
            digest = build_digest(ed, settings)
        except ValueError:
            digest = build_digest(demo_edition(settings), settings)
        return HTMLResponse(digest.html, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ newsletter
    async def subscribe(request: Request):
        if not limiter.allow(client_ip(request)):
            return error("Too many attempts. Please try again in a few minutes.", 429)
        if int(request.headers.get("content-length") or 0) > 4096:
            return error("Request too large.", 413)
        try:
            body = await request.json()
        except ValueError:
            return error("Send JSON like {\"email\": \"you@example.com\"}.", 400)
        if not isinstance(body, dict):
            return error("Send a JSON object.", 400)
        message = SUBSCRIBE_OK if settings.double_opt_in else SUBSCRIBE_OK_NO_CONFIRM
        if body.get("website"):  # honeypot: bots fill every field; pretend it worked
            return JSONResponse({"ok": True, "message": message})
        try:
            await run(services.newsletter.subscribe, body.get("email"), body.get("name"))
        except ValueError as exc:
            return error(str(exc), 400)
        except MailError as exc:
            log.error("subscribe: email delivery failed: %s", exc)
            return error("We couldn't send the confirmation email just now. Please try again shortly.", 502)
        return JSONResponse({"ok": True, "message": message})

    async def confirm(request: Request):
        result = await run(services.newsletter.confirm, request.query_params.get("token", ""))
        if not result["ok"]:
            return page("Link expired", "That link didn't work", "It may have expired or already been replaced. Subscribe again from the front page and we'll send a fresh one.", ok=False, status=400)
        return page("Subscribed", "You're in.", f"Welcome aboard. Your first briefing is on its way or arrives at {settings.send_time} ({settings.timezone}) tomorrow.")

    async def unsubscribe(request: Request):
        token = request.query_params.get("token", "")
        if request.method == "POST":  # one-click (RFC 8058) from mail clients, or the button below
            result = await run(services.newsletter.unsubscribe, token)
            if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded") and "list-unsubscribe" in (await request.body()).decode("utf-8", "ignore").lower():
                return Response(status_code=200 if result["ok"] else 404)
            if not result["ok"]:
                return page("Link expired", "That link didn't work", "We couldn't find that subscription.", ok=False, status=404)
            return page("Unsubscribed", "You're off the list.", "No more emails. Changed your mind? You can subscribe again from the front page any time.")
        sub = await run(services.newsletter.lookup, token)
        if not sub:
            return page("Link expired", "That link didn't work", "We couldn't find that subscription.", ok=False, status=404)
        if sub["status"] == "unsubscribed":
            return page("Unsubscribed", "Already unsubscribed.", "You won't receive any more emails.")
        return page(
            "Unsubscribe", "Leave the list?", "Confirm and the daily briefing stops immediately.",
            action={"url": f"/api/unsubscribe?token={token}", "label": "YES, UNSUBSCRIBE"},
        )

    # ------------------------------------------------------------------ admin
    def authorised(request: Request) -> bool:
        token = settings.admin_token
        if not token:
            return False
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return secrets.compare_digest(supplied.encode(), token.encode())

    def admin_guard(handler):
        async def guarded(request: Request):
            if not settings.admin_token:
                return error("not found", 404)  # admin API is switched off unless ADMIN_TOKEN is set
            if not authorised(request):
                return error("unauthorised", 401)
            return await handler(request)
        return guarded

    async def admin_run(request: Request):
        try:
            ed = await run(services.refresh)
        except Busy:
            return error("a run is already in progress", 409)
        except PipelineError as exc:
            return error(str(exc), 502)
        return JSONResponse({"ok": True, "date": ed["date"], "edition_no": ed["edition_no"], "stats": ed["stats"]})

    async def admin_send(request: Request):
        counts = await run(services.send_daily_job)
        if counts is None:
            return error("no live edition available to send", 409)
        return JSONResponse({"ok": True, **counts})

    async def admin_status(request: Request):
        today = local_today(settings)
        return JSONResponse(
            {
                "today": today,
                "subscribers": await run(services.db.subscriber_counts),
                "sent_today": await run(services.db.send_counts, today),
                "has_live_edition_today": (await run(services.todays_edition)) is not None,
                "last_run_at": services.last_run_at,
                "last_error": services.last_error,
                "email_mode": "smtp" if settings.smtp_enabled else "outbox",
                "llm": settings.llm_enabled,
            }
        )

    async def api_not_found(request: Request):
        return error("not found", 404)

    # ------------------------------------------------------------------ wiring
    routes: list[Any] = [
        Route("/api/health", health),
        Route("/api/meta", meta),
        Route("/api/edition", edition),
        Route("/api/refresh", refresh_news, methods=["POST"]),
        Route("/api/editions", editions),
        Route("/api/sudoku", sudoku_puzzle),
        Route("/api/email-preview", email_preview),
        Route("/api/subscribe", subscribe, methods=["POST"]),
        Route("/api/confirm", confirm),
        Route("/api/unsubscribe", unsubscribe, methods=["GET", "POST"]),
        Route("/api/admin/run", admin_guard(admin_run), methods=["POST"]),
        Route("/api/admin/send", admin_guard(admin_send), methods=["POST"]),
        Route("/api/admin/status", admin_guard(admin_status)),
        Route("/api/{rest:path}", api_not_found, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
        Mount("/", app=StaticFiles(directory=str(settings.frontend_dir), html=True, check_dir=False), name="frontend"),
    ]

    @asynccontextmanager
    async def lifespan(app: Starlette):
        tasks: list[asyncio.Task] = []
        if settings.scheduler_enabled:
            scheduler = Scheduler(settings, refresh=services.refresh_quietly, send=services.send_daily_quietly)
            tasks.append(asyncio.create_task(scheduler.run()))
            tasks.append(asyncio.create_task(asyncio.to_thread(services.bootstrap)))
        yield
        for task in tasks:
            task.cancel()

    app = Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)], lifespan=lifespan)
    app.state.services = services
    app.state.settings = settings
    return app
