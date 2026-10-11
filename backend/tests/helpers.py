"""Shared test helpers: temp settings, a seeded Services object, and a minimal in-process ASGI client
(so the API can be tested without httpx / TestClient)."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from app.agents.scout import ScoutAgent
from app.config import Settings
from app.pipeline import Pipeline
from app.services import Services

from .fakefeeds import make_fetcher


def make_settings(tmp: Path, **overrides) -> Settings:
    base = dict(
        db_path=tmp / "t.db", outbox_dir=tmp / "outbox", scheduler_enabled=False,
        send_delay_seconds=0.0, base_url="http://test.local", timezone="UTC",
    )
    base.update(overrides)
    return Settings(**base)


def make_services(tmp: Path | None = None, seeded: bool = True, **overrides) -> tuple[Services, Settings]:
    tmp = tmp or Path(tempfile.mkdtemp())
    settings = make_settings(tmp, **overrides)
    pipeline_holder = {}
    services = Services(settings)
    services.pipeline = Pipeline(settings, services.db, scout=ScoutAgent(fetcher=make_fetcher()))
    pipeline_holder["p"] = services.pipeline
    if seeded:
        services.refresh()
    return services, settings


def outbox_files(settings: Settings) -> list[Path]:
    return sorted(settings.outbox_dir.glob("*.eml")) if settings.outbox_dir.exists() else []


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status, self.headers, self.body = status, headers, body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)


def request(app, method: str, url: str, *, json_body=None, raw_body: bytes | None = None, headers: dict | None = None,
            client_ip: str = "10.0.0.1") -> Response:
    parts = urlsplit(url)
    body = raw_body if raw_body is not None else (json.dumps(json_body).encode() if json_body is not None else b"")
    hdrs = {"host": "test.local", **{k.lower(): v for k, v in (headers or {}).items()}}
    if json_body is not None:
        hdrs.setdefault("content-type", "application/json")
    hdrs["content-length"] = str(len(body))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method.upper(), "scheme": "http",
        "path": parts.path, "raw_path": parts.path.encode(), "query_string": parts.query.encode(),
        "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()], "client": (client_ip, 1234), "server": ("test.local", 80),
        "root_path": "", "state": {},
    }
    sent: list[dict] = []

    async def go():
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)

    asyncio.run(go())
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return Response(start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, payload)
