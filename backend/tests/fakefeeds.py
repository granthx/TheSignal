"""Offline fake feeds so the whole pipeline can be exercised without a network.
All names, numbers and events here are invented test data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

import requests

# (feed, title, link, description, hours_ago)
NEWS = [
    ("techcrunch", "OpenAI launches GPT-6 with stronger reasoning", "https://tc.example/gpt6?utm_source=feed", "OpenAI said its new model beats prior systems on hard reasoning tests. The release is available to paid ChatGPT users today.", 2),
    ("verge", "OpenAI releases GPT-6, a stronger reasoning model", "https://verge.example/gpt6", "A closer look at the GPT-6 release and what changed for developers.", 3),
    ("bloomberg", "OpenAI GPT-6 launch puts pressure on rivals", "https://bloomberg.example/gpt6-rivals", "Analysts say the GPT-6 launch raises the bar for Anthropic and Google DeepMind.", 3),
    ("techcrunch", "Anthropic raises funding at record valuation", "https://tc.example/anthropic-round", "Anthropic closed a new funding round led by large investors, lifting its valuation well above the last round.", 4),
    ("verge", "Anthropic's Claude Code adds background agents", "https://verge.example/claude-code", "Claude Code can now run long tasks in the background and report back, Anthropic said.", 7),
    ("wired", "Inside Meta's superintelligence lab and its hiring spree", "https://wired.example/meta-lab", "Meta superintelligence researchers describe a lab built around compute and talent. Mark Zuckerberg is closely involved.", 9),
    ("cnbc", "Meta signs multi-gigawatt data center deal for AI training", "https://cnbc.example/meta-dc", "Meta Platforms will build new data center capacity to train larger models.", 12),
    ("spacenews", "SpaceX Starship completes its latest test flight", "https://spacenews.example/starship", "Starship reached its target trajectory during the latest SpaceX flight test from Starbase.", 5),
    ("nasaspaceflight", "Starlink passes a new subscriber milestone", "https://nsf.example/starlink", "SpaceX said the Starlink constellation now serves millions more customers than a year ago.", 15),
    ("nvidia", "NVIDIA unveils next-generation AI chip for data centers", "https://nvidia.example/chip", "The new accelerator targets frontier model training with far more memory bandwidth. Jensen Huang presented it.", 6),
    ("cnbc", "TSMC lifts AI chip demand outlook", "https://cnbc.example/tsmc", "TSMC raised its outlook for AI semiconductor demand, citing GPU orders.", 10),
    ("deepmind", "Google DeepMind publishes an interpretability result on Gemini", "https://deepmind.example/interp", "Researchers trace how a Gemini model represents plans, a step toward AI safety tools.", 8),
    ("techcrunch", "AI startup raises $2 billion at a $30 billion valuation", "https://tc.example/startup-round", "The AI startup's funding round values it at $30 billion, investors said.", 11),
    ("verge", "Senate hearing debates frontier AI regulation", "https://verge.example/senate", "Lawmakers questioned AI lab executives about safety testing and the AI Act.", 13),
    ("mit", "Humanoid robot maker begins warehouse deployments", "https://mit.example/humanoid", "A humanoid robotics company says its robots are now working shifts in logistics sites.", 20),
    ("techcrunch", "Best 10 deals on headphones this Black Friday", "https://tc.example/deals", "Save big on audio gear.", 1),
    ("verge", "Taylor Swift tour breaks box office record", "https://verge.example/swift", "Celebrity news.", 2),
    ("ars", "Ancient news: AI model released last month", "https://ars.example/old", "OpenAI released something.", 90),
]
READS = [
    ("quanta", "Why intelligence might not be a single dial", "https://quanta.example/intelligence", "A physicist and a cognitive scientist argue that intelligence is many-sided, and what that means for reasoning machines and the mind.", 96, 2400),
    ("aeon", "The strange mathematics of collective decisions", "https://aeon.example/collective", "Game theory and incentives explain why smart groups sometimes make poor choices.", 150, 1800),
    ("slate", "Scaling laws, explained like a mental model", "https://slate.example/scaling", "A first-principles essay on scaling, complexity and why simple curves predict so much about AI.", 60, 3000),
    ("fs", "A recipe for sourdough", "https://fs.example/bread", "Flour and water.", 48, 400),
]
SOURCE_OF = {"techcrunch": "TechCrunch", "verge": "The Verge", "bloomberg": "Bloomberg", "wired": "Wired", "cnbc": "CNBC",
             "spacenews": "SpaceNews", "nasaspaceflight": "NASASpaceflight", "nvidia": "NVIDIA Blog", "deepmind": "Google DeepMind",
             "mit": "MIT Technology Review", "ars": "Ars Technica"}
URL_HINT = {  # which real-feed URL (see sources.yaml) serves which fake feed
    "techcrunch.com": "techcrunch", "theverge.com": "verge", "bloomberg.com": "bloomberg", "wired.com": "wired",
    "cnbc.com": "cnbc", "spacenews.com": "spacenews", "nasaspaceflight.com": "nasaspaceflight",
    "blogs.nvidia.com": "nvidia", "deepmind.google": "deepmind", "technologyreview.com": "mit",
    "arstechnica.com": "ars", "quantamagazine.org": "quanta", "aeon.co": "aeon", "worksinprogress.co": "slate",
    "fs.blog": "fs",
}


def _rss(items, now):
    body = "".join(
        f"<item><title>{escape(t)}</title><link>{escape(l)}</link><description>{escape(d)}</description>"
        f"<pubDate>{format_datetime(now - timedelta(hours=h))}</pubDate></item>"
        for t, l, d, h in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>fake</title>{body}</channel></rss>'.encode()


def _atom(entries, now):
    body = "".join(
        f'<entry><title>{escape(t)}</title><link rel="alternate" href="{escape(l)}"/><summary>{escape(d)}</summary>'
        f"<content type=\"html\">{escape(('word ' * w).strip())}</content><published>{(now - timedelta(hours=h)).isoformat()}</published></entry>"
        for t, l, d, h, w in entries
    )
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()


def make_fetcher(now: datetime | None = None):
    now = now or datetime.now(timezone.utc)

    def fetch(url: str) -> bytes:
        if "news.google.com" in url:
            raise requests.ConnectionError("blocked in tests")
        for host, key in URL_HINT.items():
            if host in url:
                if key in {"quanta", "aeon", "slate", "fs"}:
                    return _atom([(t, l, d, h, w) for k, t, l, d, h, w in READS if k == key], now)
                return _rss([(t, l, d, h) for k, t, l, d, h in NEWS if k == key], now)
        raise requests.ConnectionError("unknown host in tests")

    return fetch
