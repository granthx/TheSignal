"""A clearly-labelled SAMPLE edition shown before the first real run (or when every source is unreachable).
Nothing here is real news. Headlines are prefixed 'SAMPLE' and links point to example.com."""
from __future__ import annotations

from datetime import datetime, timedelta

from .agents.ranker import Matcher, assign_impact
from .config import Settings
from .edition import compose_edition
from .models import Article
from .pipeline import load_yaml, local_today
from .util import to_iso, utcnow

# (beat, headline, why, hours_ago, impact, entities, also)
_FIXTURES = [
    ("labs", "SAMPLE: A frontier lab ships a new reasoning model", "Placeholder copy. Run the pipeline to replace this paper with real stories.", 2, 5, ["OpenAI"], ["Demo Wire", "Sample Post"]),
    ("labs", "SAMPLE: Safety team publishes an interpretability result", "Shows how a story with a speech-bubble 'why it matters' looks.", 5, 4, ["Anthropic"], ["Demo Wire"]),
    ("labs", "SAMPLE: Social giant reorganises its superintelligence group", "Entity chips in the UI filter stories like this one.", 9, 3, ["Meta"], []),
    ("compute", "SAMPLE: Chipmaker announces next-generation accelerator", "Compute stories land in the Chips & Compute chapter.", 4, 4, [], ["Sample Post"]),
    ("compute", "SAMPLE: Hyperscaler signs multi-gigawatt power deal for data centers", "", 11, 3, [], []),
    ("space", "SAMPLE: Heavy-lift rocket completes a test flight", "Space & Rockets chapter placeholder.", 3, 4, ["SpaceX"], ["Demo Wire", "Sample Post"]),
    ("space", "SAMPLE: Satellite constellation passes a subscriber milestone", "", 14, 2, ["SpaceX"], []),
    ("bigtech", "SAMPLE: Cloud provider bundles agent tooling into its platform", "", 7, 3, [], []),
    ("money", "SAMPLE: AI startup closes a record-sized funding round", "Money & Deals chapter placeholder.", 6, 4, [], ["Demo Wire"]),
    ("policy", "SAMPLE: Lawmakers debate rules for frontier AI systems", "", 10, 2, [], []),
    ("robots", "SAMPLE: Humanoid robot maker demos warehouse deployment", "", 18, 2, [], []),
]

_READS = [
    ("SAMPLE: An essay on why intelligence might be less one-dimensional than we think", "Demo Essays", 14),
    ("SAMPLE: A physicist explains how complexity emerges from simple rules", "Sample Quarterly", 18),
    ("SAMPLE: What game theory says about racing to build powerful technology", "Demo Essays", 11),
]


def demo_edition(settings: Settings, now: datetime | None = None) -> dict:
    now = now or utcnow()
    matcher = Matcher(load_yaml(settings.topics_file))
    stories: list[Article] = []
    for i, (beat, title, why, hours, impact, entities, also) in enumerate(_FIXTURES):
        a = Article(
            title=title,
            url=f"https://example.com/sample-{i + 1}",
            source="Demo Wire",
            published=now - timedelta(hours=hours),
        )
        a.beat, a.headline, a.why, a.impact = beat, title, why, impact
        a.entities, a.also, a.cluster_size = entities, also, 1 + len(also)
        a.score = 20 + impact * 8 - hours * 0.3
        stories.append(a)
    assign_impact([s for s in stories if not s.impact])
    reads = [
        {
            "id": f"demo-read-{i}",
            "title": title,
            "url": f"https://example.com/sample-read-{i + 1}",
            "source": source,
            "hook": "Placeholder hook. In a real edition this sentence says what idea the essay hands you.",
            "read_minutes": minutes,
            "published": to_iso(now - timedelta(days=2)),
        }
        for i, (title, source, minutes) in enumerate(_READS)
    ]
    edition = compose_edition(
        stories=stories,
        reads=reads,
        matcher=matcher,
        lead_id=stories[0].id,
        ticker=[s.headline[:70] for s in stories[:8]],
        now=now,
        timezone=settings.timezone,
        mode="demo",
        stats={
            "sources_ok": 0, "sources_failed": 0, "failed_sources": [], "articles_seen": 0,
            "unique_stories": 0, "cleared_the_bar": 0, "published": len(stories), "llm": False,
        },
    )
    edition["date"] = local_today(settings, now)
    edition["edition_no"] = 0
    return edition
