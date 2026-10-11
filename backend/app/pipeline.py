"""Pipeline: orchestrates the agents (scout -> dedupe -> rank -> edit -> brain -> compose -> store)."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .agents import dedupe, ranker
from .agents.brain import BrainAgent
from .agents.editor import EditorAgent
from .agents.ranker import Matcher
from .agents.scout import ScoutAgent, build_specs
from .config import Settings
from .db import Database
from .edition import compose_edition
from .llm import make_llm
from .util import canonical_url, utcnow

log = logging.getLogger("signal.pipeline")


class PipelineError(Exception):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def local_today(settings: Settings, now: datetime | None = None) -> str:
    return (now or utcnow()).astimezone(ZoneInfo(settings.timezone)).date().isoformat()


class Pipeline:
    def __init__(self, settings: Settings, db: Database, llm=None, scout: ScoutAgent | None = None):
        self.s = settings
        self.db = db
        self.llm = llm if llm is not None else make_llm(settings)
        self.scout = scout or ScoutAgent()

    def _seen(self, today: str, days: int) -> tuple[set[str], list[str]]:
        urls: set[str] = set()
        titles: list[str] = []
        for ed in self.db.recent_editions(today, days):
            stories = ([ed["lead"]] if ed.get("lead") else []) + [s for sec in ed.get("sections", []) for s in sec["stories"]]
            for st in stories:
                urls.add(canonical_url(st["url"]))
                titles.append(st.get("title") or st["headline"])
        return urls, titles

    def _used_reads(self, today: str, days: int) -> set[str]:
        return {canonical_url(r["url"]) for ed in self.db.recent_editions(today, days) for r in ed.get("reads", [])}

    def run(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        today = local_today(self.s, now)
        topics = load_yaml(self.s.topics_file)
        sources = load_yaml(self.s.sources_file)
        sel = topics.get("selection", {})
        matcher = Matcher(topics)

        scouted = self.scout.scout(build_specs(sources))
        if not scouted.articles:
            reasons = "; ".join(f"{r['name']}: {r['error']}" for r in scouted.failed[:3])
            raise PipelineError(f"no articles could be fetched from any source ({reasons or 'no sources configured'})")

        news = [a for a in scouted.articles if a.kind == "news"]
        reads = [a for a in scouted.articles if a.kind == "read"]

        unique = dedupe.cluster(news)
        seen_urls, seen_titles = self._seen(today, days=2)
        fresh = dedupe.drop_seen(unique, seen_urls, seen_titles)
        ranked = ranker.rank(fresh, matcher, now)
        candidates = ranked[: int(sel.get("editor_candidates", 28))]

        edit = EditorAgent(self.llm).edit(candidates, matcher.beats)
        ranker.assign_impact([s for s in edit.articles if not s.impact])

        read_cfg = topics.get("reads", {})
        used = self._used_reads(today, int(read_cfg.get("repeat_window_days", 45)))
        picks = BrainAgent(self.llm, matcher).pick(reads, used, now, read_cfg)

        stats = {
            "sources_ok": len(scouted.ok),
            "sources_failed": len(scouted.failed),
            "failed_sources": [{"name": r["name"], "error": r["error"]} for r in scouted.failed],
            "articles_seen": len(news),
            "unique_stories": len(unique),
            "cleared_the_bar": len(ranked),
            "published": len(edit.articles),
            "llm": edit.used_llm,
        }
        edition = compose_edition(
            stories=edit.articles,
            reads=picks,
            matcher=matcher,
            lead_id=edit.lead_id,
            ticker=edit.ticker,
            now=now,
            timezone=self.s.timezone,
            mode="live",
            stats=stats,
        )
        saved = self.db.save_edition(today, edition)
        log.info(
            "edition %s #%s: %d stories (%d candidates, llm=%s), %d reads",
            today, saved["edition_no"], len(edit.articles), len(ranked), edit.used_llm, len(picks),
        )
        return saved
