"""ScoutAgent: fetches every configured feed in parallel and turns entries into Articles."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote_plus

import requests

from ..feeds import FeedError, parse_feed
from ..models import Article
from ..util import clean_text

log = logging.getLogger("signal.scout")

USER_AGENT = "TheSignalBot/1.0 (personal news aggregator; reads public RSS feeds)"
Fetcher = Callable[[str], bytes]


@dataclass
class SourceSpec:
    name: str
    url: str
    weight: float = 1.0
    kind: str = "news"  # "news" | "read"
    beat_hint: str | None = None


@dataclass
class ScoutResult:
    articles: list[Article] = field(default_factory=list)
    report: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> list[str]:
        return [r["name"] for r in self.report if r["ok"]]

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.report if not r["ok"]]


def http_fetch(url: str, timeout: tuple[float, float] = (6, 15), max_bytes: int = 4_000_000) -> bytes:
    """GET with a hard size cap. Raises requests exceptions on network / HTTP errors."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    }
    with requests.get(url, headers=headers, timeout=timeout, stream=True) as resp:
        resp.raise_for_status()
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            size += len(chunk)
            if size > max_bytes:
                raise FeedError("feed larger than the size cap")
            chunks.append(chunk)
    return b"".join(chunks)


def build_specs(sources_cfg: dict) -> list[SourceSpec]:
    """Expand sources.yaml (including the Google News query shortcut) into concrete feeds."""
    specs: list[SourceSpec] = []
    gn = sources_cfg.get("google_news") or {}
    for item in gn.get("queries", []):
        q = item["q"] if isinstance(item, dict) else str(item)
        query = f"{q} when:{gn.get('when', '1d')}"
        url = (
            "https://news.google.com/rss/search?q=" + quote_plus(query)
            + f"&hl={gn.get('hl', 'en-US')}&gl={gn.get('gl', 'US')}&ceid={quote_plus(str(gn.get('ceid', 'US:en')))}"
        )
        specs.append(SourceSpec(name=f"Google News · {q}", url=url, weight=float(gn.get("weight", 1.0))))
    for item in sources_cfg.get("news", []) or []:
        specs.append(SourceSpec(item["name"], item["url"], float(item.get("weight", 1.0)), "news", item.get("beat")))
    for item in sources_cfg.get("reads", []) or []:
        specs.append(SourceSpec(item["name"], item["url"], float(item.get("weight", 1.0)), "read", item.get("beat")))
    return specs


class ScoutAgent:
    def __init__(self, fetcher: Fetcher = http_fetch, max_workers: int = 10):
        self.fetcher = fetcher
        self.max_workers = max_workers

    def _scout_one(self, spec: SourceSpec) -> tuple[SourceSpec, list[Article], str | None]:
        try:
            raw = self.fetcher(spec.url)
            entries = parse_feed(raw)
        except FeedError as exc:
            return spec, [], f"feed error: {exc}"
        except requests.RequestException as exc:
            return spec, [], f"network error: {type(exc).__name__}: {str(exc)[:120]}"
        except Exception as exc:  # noqa: BLE001 - one bad feed must never sink the run
            return spec, [], f"unexpected error: {type(exc).__name__}: {str(exc)[:120]}"

        is_google = "news.google.com" in spec.url
        articles: list[Article] = []
        for e in entries:
            title, source = e["title"], e["source_name"] or spec.name
            if is_google and e["source_name"]:
                # Google News appends " - Outlet" to every title; the outlet already lives in <source>.
                title = re.sub(rf"\s+[-–—]\s+{re.escape(e['source_name'])}\s*$", "", title)
            summary = e["summary"]
            if summary and (summary.lower().startswith(title.lower()[:40]) or is_google):
                summary = ""  # Google News descriptions only repeat the headline and related links
            articles.append(
                Article(
                    title=clean_text(title),
                    url=e["link"],
                    source=source,
                    summary=summary,
                    published=e["published"],
                    kind=spec.kind,
                    weight=spec.weight,
                    content_words=e["content_words"],
                    beat_hint=spec.beat_hint,
                )
            )
        return spec, articles, None

    def scout(self, specs: list[SourceSpec]) -> ScoutResult:
        result = ScoutResult()
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for spec, articles, error in pool.map(self._scout_one, specs):
                result.report.append(
                    {"name": spec.name, "kind": spec.kind, "ok": error is None, "count": len(articles), "error": error}
                )
                if error:
                    log.warning("source failed: %s (%s)", spec.name, error)
                result.articles.extend(articles)
        log.info("scouted %d articles from %d/%d sources", len(result.articles), len(result.ok), len(specs))
        return result
