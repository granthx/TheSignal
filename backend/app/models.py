"""Plain data containers passed between the agents."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .util import canonical_url, make_id, to_iso


@dataclass
class Article:
    title: str
    url: str
    source: str
    summary: str = ""
    published: datetime | None = None
    kind: str = "news"  # "news" | "read"
    weight: float = 1.0  # trust / priority of the feed it came from
    content_words: int = 0  # words in the full text when the feed ships it
    beat_hint: str | None = None

    # --- filled in by the agents
    id: str = ""
    score: float = 0.0
    beat: str = ""
    entities: list[str] = field(default_factory=list)
    hits: list[str] = field(default_factory=list)
    cluster_size: int = 1
    also: list[str] = field(default_factory=list)
    headline: str = ""
    why: str = ""
    impact: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = make_id(canonical_url(self.url))

    def to_story(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "headline": self.headline or self.title,
            "why": self.why,
            "summary": self.summary[:300],
            "beat": self.beat,
            "source": self.source,
            "url": self.url,
            "published": to_iso(self.published),
            "impact": self.impact or 1,
            "score": round(self.score, 1),
            "entities": list(self.entities),
            "also": list(self.also),
        }
