"""Dependency-free RSS 2.0 / RSS 1.0 (RDF) / Atom parser built on the standard library.

Feeds are untrusted input, so documents that declare XML entities (billion-laughs style
expansion attacks) are rejected outright instead of parsed.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import entities as html_entities
from email.utils import parsedate_to_datetime
from typing import Any

from .util import clean_text, word_count

MAX_FEED_BYTES = 4_000_000


class FeedError(Exception):
    """The document could not be understood as a feed."""


_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_NAMED_ENTITY = re.compile(rb"&([A-Za-z][A-Za-z0-9]{1,31});")
_BARE_AMP = re.compile(rb"&(?!(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);)")


def _repair_entities(data: bytes) -> bytes:
    """Real feeds often contain HTML entities (&nbsp;, &mdash;) or bare ampersands that strict XML rejects.
    Convert them to numeric references / &amp; so one sloppy publisher does not lose the whole feed."""

    def named(match: re.Match[bytes]) -> bytes:
        name = match.group(1).decode("ascii")
        if name in _XML_ENTITIES:
            return match.group(0)
        codepoint = html_entities.name2codepoint.get(name)
        return f"&#{codepoint};".encode() if codepoint else b"&amp;" + match.group(1) + b";"

    return _BARE_AMP.sub(b"&amp;", _NAMED_ENTITY.sub(named, data))

def _local(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _children(el: ET.Element) -> dict[str, list[ET.Element]]:
    out: dict[str, list[ET.Element]] = {}
    for child in el:
        out.setdefault(_local(child.tag), []).append(child)
    return out


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    dt: datetime | None = None
    try:
        dt = parsedate_to_datetime(value)  # RFC 822, used by RSS
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))  # ISO 8601, used by Atom
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _atom_link(links: list[ET.Element]) -> str:
    fallback = ""
    for link in links:
        href = (link.get("href") or "").strip()
        if not href:
            continue
        rel = (link.get("rel") or "alternate").lower()
        if rel == "alternate":
            return href
        fallback = fallback or href
    return fallback


def _first_text(kids: dict[str, list[ET.Element]], *names: str) -> str:
    for name in names:
        if name in kids:
            value = _text(kids[name][0])
            if value:
                return value
    return ""


def parse_feed(data: bytes) -> list[dict[str, Any]]:
    """Return a list of ``{title, link, summary, published, source_name, content_words}`` dicts."""
    if not data:
        raise FeedError("empty response")
    if len(data) > MAX_FEED_BYTES:
        raise FeedError("feed too large")
    data = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if re.search(rb"<!ENTITY", data[:200_000], re.IGNORECASE):
        raise FeedError("feeds declaring XML entities are refused")
    if data[:15].lower().startswith(b"<!doctype html") or data[:6].lower() == b"<html>":
        raise FeedError("got an HTML page, not a feed")
    try:
        root = ET.fromstring(_repair_entities(data))
    except ET.ParseError as exc:
        raise FeedError(f"invalid XML: {exc}") from exc

    root_kind = _local(root.tag)
    items: list[dict[str, Any]] = []

    if root_kind in ("rss", "rdf"):
        entries = [el for el in root.iter() if _local(el.tag) == "item"]
        for el in entries:
            kids = _children(el)
            link = _first_text(kids, "link") or _first_text(kids, "guid")
            body = _first_text(kids, "encoded", "content")
            summary_raw = _first_text(kids, "description", "summary")
            items.append(
                {
                    "title": clean_text(_first_text(kids, "title")),
                    "link": link.strip(),
                    "summary": clean_text(summary_raw or body, limit=600),
                    "published": parse_date(_first_text(kids, "pubdate", "date", "published", "updated")),
                    "source_name": clean_text(_first_text(kids, "source")),
                    "content_words": word_count(clean_text(body)) if body else 0,
                }
            )
    elif root_kind == "feed":
        for el in (e for e in root if _local(e.tag) == "entry"):
            kids = _children(el)
            body = _first_text(kids, "content")
            items.append(
                {
                    "title": clean_text(_first_text(kids, "title")),
                    "link": _atom_link(kids.get("link", [])),
                    "summary": clean_text(_first_text(kids, "summary") or body, limit=600),
                    "published": parse_date(_first_text(kids, "published", "updated")),
                    "source_name": "",
                    "content_words": word_count(clean_text(body)) if body else 0,
                }
            )
    else:
        raise FeedError(f"unrecognised root element <{root_kind}>")

    return [i for i in items if i["title"] and i["link"].lower().startswith(("http://", "https://"))]
