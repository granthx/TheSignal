"""Collapse near-identical headlines from different outlets into one story, and drop repeats from
previous editions. Pure heuristics (token overlap + sequence similarity), no model needed."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from ..models import Article
from ..util import canonical_url

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "from", "as", "is", "are",
    "was", "were", "be", "its", "it", "this", "that", "new", "says", "say", "said", "after", "over", "will",
    "how", "why", "what", "who", "into", "than", "more", "about", "just", "now", "report", "reports",
}
_NON_ALNUM = re.compile(r"[^a-z0-9$%.\- ]+")


def _norm(title: str) -> str:
    t = title.lower().replace("’", "'")
    t = re.sub(r"\s+[-–—|]\s+[^-–—|]{2,40}$", "", t)  # trailing " - Outlet"
    t = _NON_ALNUM.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(title: str) -> set[str]:
    out = set()
    for tok in _norm(title).split():
        tok = tok.strip(".-")
        if len(tok) < 2 or tok in _STOP:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.add(tok)
    return out


def _anchors(toks: set[str]) -> set[str]:
    return {t for t in toks if any(c.isdigit() for c in t)}


def similar(a: set[str], b: set[str], norm_a: str, norm_b: str) -> bool:
    if not a or not b:
        return False
    anchors_a, anchors_b = _anchors(a), _anchors(b)
    # "GPT-5" vs "GPT-6", "$6B" vs "$10B": different facts even if the sentence shape matches.
    if anchors_a and anchors_b and not (anchors_a & anchors_b):
        return False
    jaccard = len(a & b) / len(a | b)
    threshold = 0.45 if anchors_a & anchors_b else 0.6
    if jaccard >= threshold:
        return True
    return SequenceMatcher(None, norm_a, norm_b).ratio() >= 0.88


def cluster(articles: list[Article]) -> list[Article]:
    """Return one representative per story. Representatives carry ``cluster_size`` and ``also``."""
    prepared = [(a, tokens(a.title), _norm(a.title)) for a in articles]
    groups: list[list[tuple[Article, set[str], str]]] = []
    by_url: dict[str, int] = {}

    for item in prepared:
        article, toks, norm = item
        url_key = canonical_url(article.url)
        if url_key in by_url:
            groups[by_url[url_key]].append(item)
            continue
        placed = False
        for idx, group in enumerate(groups):
            _, g_toks, g_norm = group[0]
            if similar(toks, g_toks, norm, g_norm):
                group.append(item)
                by_url[url_key] = idx
                placed = True
                break
        if not placed:
            groups.append([item])
            by_url[url_key] = len(groups) - 1

    reps: list[Article] = []
    for group in groups:
        members = [m[0] for m in group]
        best = max(members, key=lambda a: (a.weight, len(a.summary), a.published.timestamp() if a.published else 0))
        outlets: list[str] = []
        for m in members:
            if m.source != best.source and m.source not in outlets:
                outlets.append(m.source)
        best.also = outlets
        best.cluster_size = 1 + len(outlets)
        reps.append(best)
    return reps


def drop_seen(articles: list[Article], seen_urls: set[str], seen_titles: list[str]) -> list[Article]:
    """Remove stories already featured in a previous edition (same URL or near-identical headline)."""
    seen_tok = [(tokens(t), _norm(t)) for t in seen_titles]
    kept = []
    for a in articles:
        if canonical_url(a.url) in seen_urls:
            continue
        toks, norm = tokens(a.title), _norm(a.title)
        if any(similar(toks, s_toks, norm, s_norm) for s_toks, s_norm in seen_tok):
            continue
        kept.append(a)
    return kept
