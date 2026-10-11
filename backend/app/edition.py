"""Assembles the final edition JSON that both the website and the newsletter are rendered from."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .agents.ranker import Matcher
from .models import Article
from .util import to_iso


def compose_edition(
    *,
    stories: list[Article],
    reads: list[dict],
    matcher: Matcher,
    lead_id: str | None,
    ticker: list[str],
    now: datetime,
    timezone: str,
    mode: str,
    stats: dict[str, Any],
) -> dict[str, Any]:
    max_per_section = int(matcher.cfg.get("max_per_section", 6))
    lead = next((s for s in stories if s.id == lead_id), None) or (
        max(stories, key=lambda s: (s.impact, s.score)) if stories else None
    )
    rest = [s for s in stories if s is not lead]

    sections = []
    for beat_id in matcher.beat_order:
        members = sorted((s for s in rest if s.beat == beat_id), key=lambda s: (-s.impact, -s.score))
        members = members[:max_per_section]
        if members:
            beat = matcher.beats[beat_id]
            sections.append(
                {"id": beat_id, "title": beat["title"], "kicker": beat["kicker"], "stories": [m.to_story() for m in members]}
            )

    shown_ids = {st["id"] for sec in sections for st in sec["stories"]}
    shown = ([lead] if lead else []) + [s for s in rest if s.id in shown_ids]
    top = sorted(shown, key=lambda s: (-s.impact, -s.score))[:5]

    watchlist = {e.name: 0 for e in matcher.entities}
    for s in shown:
        for name in s.entities:
            watchlist[name] = watchlist.get(name, 0) + 1

    return {
        "mode": mode,
        "generated_at": to_iso(now),
        "timezone": timezone,
        "beat_titles": {bid: b["title"] for bid, b in matcher.beats.items()},
        "lead": lead.to_story() if lead else None,
        "top_ids": [s.id for s in top],
        "sections": sections,
        "reads": reads,
        "ticker": ticker,
        "watchlist": watchlist,
        "stats": stats,
    }
