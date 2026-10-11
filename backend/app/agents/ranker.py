"""RankerAgent: decides what is relevant to *this* reader and how much it matters.

Transparent on purpose. A story's score is
    (keyword relevance + watchlist bonus) x outlet weight x freshness x noise demotion + coverage bonus
and everything is configured in topics.yaml.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..models import Article


def _compile(keyword: str) -> re.Pattern[str]:
    # Word-boundary match with an optional plural, so "llm" matches "LLMs" but "ai" never matches "said".
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?:s|es)?(?![a-z0-9])")


@dataclass
class Entity:
    name: str
    patterns: list[re.Pattern[str]]
    bare: re.Pattern[str] | None = None
    context: list[re.Pattern[str]] = field(default_factory=list)

    def found_in(self, text: str) -> bool:
        if any(p.search(text) for p in self.patterns):
            return True
        if self.bare and self.bare.search(text):
            return any(c.search(text) for c in self.context)
        return False


@dataclass
class Analysis:
    beat_scores: dict[str, float]
    core: float
    hits: list[str]
    entities: list[str]
    entities_in_title: list[str]
    noise: str | None  # "drop" | "demote" | None


def _collect(group: str | None, patterns: list[tuple[re.Pattern[str], float, str]], title: str, summary: str) -> list[dict]:
    """Every keyword of ``group`` found in the title (counts double) or, failing that, the summary."""
    found = []
    for rx, weight, keyword in patterns:
        if rx.search(title):
            found.append({"group": group, "kw": keyword, "pts": 2 * weight, "where": "t"})
        elif rx.search(summary):
            found.append({"group": group, "kw": keyword, "pts": weight, "where": "s"})
    return found


def _drop_overlaps(found: list[dict]) -> list[dict]:
    """A keyword contained in a longer matched keyword is the same mention counted twice
    ("deepmind" inside "google deepmind", "claude" inside "claude code"), so only the longest scores."""
    kept = []
    for e in found:
        inner = re.compile(r"(?<![a-z0-9])" + re.escape(e["kw"].lower()) + r"(?![a-z0-9])")
        shadowed = any(
            o is not e and o["where"] == e["where"] and o["kw"] != e["kw"] and inner.search(o["kw"].lower())
            for o in found
        )
        if not shadowed:
            kept.append(e)
    return kept


class Matcher:
    def __init__(self, topics: dict):
        self.cfg = topics.get("selection", {})
        self.beats: dict[str, dict] = {}
        for beat_id, beat in (topics.get("beats") or {}).items():
            self.beats[beat_id] = {
                "title": beat.get("title", beat_id.title()),
                "kicker": beat.get("kicker", ""),
                "patterns": [(_compile(str(k)), float(w), str(k)) for k, w in (beat.get("keywords") or {}).items()],
            }
        self.core = [(_compile(str(k)), float(w), str(k)) for k, w in (topics.get("core") or {}).items()]
        self.entities: list[Entity] = []
        for name, spec in (topics.get("watchlist") or {}).items():
            self.entities.append(
                Entity(
                    name=name,
                    patterns=[_compile(str(m)) for m in spec.get("match", [])],
                    bare=_compile(str(spec["bare"])) if spec.get("bare") else None,
                    context=[_compile(str(c)) for c in spec.get("context", [])],
                )
            )
        noise = topics.get("noise") or {}
        self.noise_drop = [re.compile(p, re.IGNORECASE) for p in noise.get("drop", [])]
        self.noise_demote = [re.compile(p, re.IGNORECASE) for p in noise.get("demote", [])]

    @property
    def beat_order(self) -> list[str]:
        return list(self.beats)

    def analyze(self, title: str, summary: str) -> Analysis:
        t, s = title.lower(), summary.lower()
        found: list[dict] = []
        for beat_id, beat in self.beats.items():
            found.extend(_collect(beat_id, beat["patterns"], t, s))
        found.extend(_collect(None, self.core, t, s))
        found = _drop_overlaps(found)
        beat_scores = {beat_id: 0.0 for beat_id in self.beats}
        core = 0.0
        for e in found:
            if e["group"] is None:
                core += e["pts"]
            else:
                beat_scores[e["group"]] += e["pts"]
        hits = [e["kw"] for e in sorted(found, key=lambda e: -e["pts"])]
        both = f"{t} {s}"
        entities = [e.name for e in self.entities if e.found_in(both)]
        in_title = [e.name for e in self.entities if e.found_in(t)]
        noise = None
        if any(rx.search(title) for rx in self.noise_drop):
            noise = "drop"
        elif any(rx.search(title) for rx in self.noise_demote):
            noise = "demote"
        return Analysis(beat_scores, core, hits, entities, in_title, noise)


def freshness_factor(published: datetime | None, now: datetime, max_age_hours: float) -> float | None:
    """1.0 for the first 6 hours, decaying to 0.5 at max age; None means too old to publish."""
    if published is None:
        return 0.8
    age_h = max(0.0, (now - published).total_seconds() / 3600)
    if age_h > max_age_hours:
        return None
    if age_h <= 6:
        return 1.0
    return 1.0 - 0.5 * (age_h - 6) / max(1.0, max_age_hours - 6)


def rank(articles: list[Article], matcher: Matcher, now: datetime) -> list[Article]:
    """Score ``articles`` in place and return only those that clear ``selection.min_score``."""
    cfg = matcher.cfg
    min_score = float(cfg.get("min_score", 14))
    max_age = float(cfg.get("max_age_hours", 36))
    bonus_title = float(cfg.get("watchlist_bonus_title", 8))
    bonus_summary = float(cfg.get("watchlist_bonus_summary", 3))
    cluster_bonus = float(cfg.get("cluster_bonus", 4))

    kept: list[Article] = []
    for a in articles:
        factor = freshness_factor(a.published, now, max_age)
        if factor is None:
            continue
        analysis = matcher.analyze(a.title, a.summary)
        if analysis.noise == "drop":
            continue

        best_beat = max(analysis.beat_scores, key=lambda b: analysis.beat_scores[b], default=None)
        # Main section counts in full; other sections only add a third, so a story that name-drops
        # many topics cannot outrank one that is squarely about the thing you care about.
        ordered = sorted(analysis.beat_scores.values(), reverse=True)
        beat_total = (ordered[0] + 0.35 * sum(ordered[1:])) if ordered else 0.0
        relevance = beat_total + analysis.core
        for name in analysis.entities:
            relevance += bonus_title if name in analysis.entities_in_title else bonus_summary

        score = relevance * a.weight * factor
        if analysis.noise == "demote":
            score *= 0.5
        gate = score  # coverage bonus must not rescue an irrelevant story
        score += cluster_bonus * min(a.cluster_size - 1, 4) if gate >= 0.7 * min_score else 0.0

        if score < min_score:
            continue

        a.score = score
        a.entities = analysis.entities
        a.hits = analysis.hits[:8]
        if best_beat and analysis.beat_scores[best_beat] > 0:
            a.beat = best_beat
        elif a.beat_hint in matcher.beats:
            a.beat = a.beat_hint
        else:
            a.beat = matcher.beat_order[0]
        kept.append(a)

    kept.sort(key=lambda x: x.score, reverse=True)
    return kept


def assign_impact(stories: list[Article]) -> None:
    """Fallback impact (1-5) from rank percentile plus coverage, used when no LLM rated the story."""
    n = len(stories)
    for i, s in enumerate(sorted(stories, key=lambda x: x.score, reverse=True)):
        pct = i / max(1, n)
        impact = 5 if pct < 0.10 else 4 if pct < 0.30 else 3 if pct < 0.60 else 2 if pct < 0.85 else 1
        if s.cluster_size >= 3:
            impact = max(impact, 4)
        s.impact = impact
