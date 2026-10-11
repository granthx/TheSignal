"""DigestAgent: condenses an edition into the 5-minute email (HTML + plain text).

The reading time is computed from the actual rendered text (225 words per minute), and stories are added
by impact until the word budget is used, so "5 minutes" is a measurement, not a slogan."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from .config import Settings

TEMPLATES = Path(__file__).parent / "templates"
WORDS_PER_MINUTE = 225

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    autoescape=lambda name: bool(name) and name.endswith(".html.j2"),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass
class Digest:
    subject: str
    html: str
    text: str
    minutes: int
    words: int


def impact_bars(n: int) -> str:
    n = max(1, min(5, int(n or 1)))
    return "■" * n + "□" * (5 - n)


def _trim_words(text: str, limit: int) -> str:
    words = (text or "").split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip(",;:.") + "…"


def _date_label(date: str) -> str:
    try:
        return datetime.strptime(date, "%Y-%m-%d").strftime("%a %d %b %Y").upper()
    except ValueError:
        return date.upper()


def _select(edition: dict[str, Any], budget: int) -> tuple[list[dict], list[dict], list[dict]]:
    lead = edition.get("lead")
    pool = [s for sec in edition.get("sections", []) for s in sec["stories"]]
    pool.sort(key=lambda s: (-s.get("impact", 1), -s.get("score", 0)))
    ordered = ([lead] if lead else []) + pool
    titles = edition.get("beat_titles", {})

    used = 70  # masthead, headings, footer
    big: list[dict] = []
    for i, s in enumerate(ordered[:3], 1):
        why = _trim_words(s.get("why", ""), 38)
        used += len(s["headline"].split()) + len(why.split()) + 8  # + label, source, impact, URL
        big.append({**s, "n": i, "why": why, "bars": impact_bars(s.get("impact", 1)), "beat_title": titles.get(s.get("beat"), "")})

    reads = []
    for r in edition.get("reads", [])[:2]:
        hook = _trim_words(r.get("hook", ""), 36)
        used += len(r["title"].split()) + len(hook.split()) + 9
        reads.append({**r, "hook": hook})

    hits: list[dict] = []
    for s in ordered[3:]:
        cost = len(s["headline"].split()) + 5  # + source and URL
        if used + cost > budget or len(hits) >= 12:
            break
        used += cost
        hits.append(s)

    groups: list[dict] = []
    for beat_id, title in titles.items():
        members = [h for h in hits if h.get("beat") == beat_id]
        if members:
            groups.append({"title": title, "stories": members})
    return big, groups, reads


def build_digest(edition: dict[str, Any], settings: Settings, unsubscribe_url: str | None = None) -> Digest:
    if not edition.get("lead"):
        raise ValueError("edition has no stories to send")
    big, groups, reads = _select(edition, settings.digest_word_budget)
    date = edition.get("date", "")
    ctx = {
        "site_name": settings.site_name,
        "subject": f"{settings.site_name} · {_date_label(date).title()[:-5].strip()} · {_trim_words(edition['lead']['headline'], 12)}",
        "preheader": " · ".join(b["headline"] for b in big[:2]),
        "date_label": _date_label(date),
        "edition_no": edition.get("edition_no", 0),
        "big": big,
        "groups": groups,
        "reads": reads,
        "demo": edition.get("mode") == "demo",
        "base_url": settings.base_url,
        "play_url": f"{settings.base_url}/#play",
        "view_url": f"{settings.base_url}/?date={date}",
        "unsubscribe_url": unsubscribe_url or f"{settings.base_url}/api/unsubscribe?token=PREVIEW",
        "minutes": 0,
    }
    # Render once to measure, then again with the measured reading time baked in.
    words = len(env.get_template("digest.txt.j2").render(**ctx).split())
    ctx["minutes"] = max(1, math.ceil(words / WORDS_PER_MINUTE))
    text = env.get_template("digest.txt.j2").render(**ctx)
    html = env.get_template("digest.html.j2").render(**ctx)
    return Digest(ctx["subject"], html, text, ctx["minutes"], words)


def render_confirmation(settings: Settings, confirm_url: str) -> tuple[str, str, str]:
    subject = f"Confirm your {settings.site_name} subscription"
    ctx = {"site_name": settings.site_name, "confirm_url": confirm_url, "send_time": settings.send_time, "timezone": settings.timezone}
    return subject, env.get_template("confirm.html.j2").render(**ctx), env.get_template("confirm.txt.j2").render(**ctx)


def render_page(settings: Settings, *, title: str, heading: str, message: str, ok: bool = True, action: dict | None = None) -> str:
    return env.get_template("page.html.j2").render(
        site_name=settings.site_name, title=title, heading=heading, message=message, ok=ok, action=action, base_url=settings.base_url
    )
