"""Small text / time helpers shared by the agents."""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
_TAG = re.compile(r"(?s)<[^>]+>")
_WS = re.compile(r"\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“‘(])")
_TRACKING_PARAMS = {
    "fbclid", "gclid", "ref", "ref_src", "cmp", "ocid", "ito", "mc_cid", "mc_eid", "guccounter",
}


def clean_text(raw: str | None, limit: int = 0) -> str:
    """Strip HTML, unescape entities, collapse whitespace, optionally truncate on a word boundary."""
    if not raw:
        return ""
    text = _SCRIPT_STYLE.sub(" ", raw)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-–—") + "…"
    return text


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text or ""))


def first_sentences(text: str, max_chars: int) -> str:
    """Take whole sentences from the start of ``text`` until ``max_chars`` would be exceeded."""
    text = clean_text(text)
    if not text:
        return ""
    out = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > max_chars:
            break
        out = candidate
    if out:
        return out
    return clean_text(text, limit=max_chars)


def canonical_url(url: str) -> str:
    """Normalise a URL so the same article under different tracking params hashes the same."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def make_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_http_url(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))
