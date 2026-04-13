"""
RSS Scraper
-----------
Polls a curated set of prediction-market-relevant RSS feeds and returns
a flat list of RawSignal objects.

Run standalone to smoke-test:
  python scrapers/rss_scraper.py
"""

from __future__ import annotations

import os
import sys

# Allow running this file directly from the project root
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import re
from datetime import datetime
from typing import Optional

import feedparser
from bs4 import BeautifulSoup

from src.models import RawSignal

# ── Feed registry ──────────────────────────────────────────────────────────────
# Ordered by signal quality for prediction markets.

RSS_FEEDS: dict[str, str] = {
    # Metaculus — live expert prediction questions (gold source)
    "metaculus":    "https://www.metaculus.com/questions/rss/",
    # Politics / macro — most relevant for prediction markets
    "bbc_world":    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_politics": "http://feeds.bbci.co.uk/news/politics/rss.xml",
    "politico":     "https://rss.politico.com/politics-news.xml",
    # Economics / markets
    "reuters":      "https://feeds.reuters.com/reuters/topNews",
    "coindesk":     "https://www.coindesk.com/arc/outboundfeeds/rss/",
}

# Topic-keyword → feeds to PRIORITISE (others still run but these go first)
_TOPIC_FEEDS: dict[str, list[str]] = {
    "crypto":    ["coindesk", "reuters", "bbc_world"],
    "bitcoin":   ["coindesk", "reuters", "bbc_world"],
    "btc":       ["coindesk", "reuters", "bbc_world"],
    "fed":       ["reuters", "bbc_world", "politico"],
    "rate":      ["reuters", "bbc_world", "politico"],
    "recession": ["reuters", "politico", "bbc_world"],
    "tariff":    ["reuters", "politico", "bbc_world"],
    "election":  ["politico", "bbc_politics", "bbc_world"],
    "shutdown":  ["politico", "bbc_politics", "reuters"],
    "congress":  ["politico", "bbc_politics", "reuters"],
    "trump":     ["politico", "bbc_politics", "reuters"],
}

_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Fast HTML stripping — falls back to regex if BS4 fails."""
    try:
        return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)
    except Exception:
        return _HTML_TAG.sub(" ", text).strip()


def _parse_date(entry) -> Optional[datetime]:
    tp = getattr(entry, "published_parsed", None)
    if tp:
        try:
            return datetime(*tp[:6])
        except Exception:
            pass
    return None


# ── Per-feed fetcher ───────────────────────────────────────────────────────────

def fetch_feed(name: str, url: str, max_items: int = 10) -> list[RawSignal]:
    """Fetch one RSS feed and return up to *max_items* RawSignals."""
    signals: list[RawSignal] = []
    try:
        feed = feedparser.parse(url, agent="PolySignal/1.0 (research bot)")

        if feed.bozo and not feed.entries:
            # bozo=True means malformed feed but may still have entries
            return signals

        for entry in feed.entries[:max_items]:
            title = getattr(entry, "title", "").strip()
            if not title:
                continue

            # Prefer summary, fall back to description or content
            raw_body = (
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
                or "".join(c.get("value", "") for c in getattr(entry, "content", []))
            )
            text = _strip_html(raw_body)[:600]

            tags = [t.get("term", "") for t in getattr(entry, "tags", []) if t.get("term")]

            signals.append(
                RawSignal(
                    source=name,
                    url=getattr(entry, "link", url),
                    title=title,
                    text=text,
                    published=_parse_date(entry),
                    tags=tags,
                    metadata={
                        "feed_url": url,
                        "feed_title": feed.feed.get("title", name),
                    },
                )
            )
    except Exception as exc:
        print(f"  [RSS] {name}: fetch error — {exc}")
    return signals


# ── Multi-feed aggregator ──────────────────────────────────────────────────────

def scrape_all(
    max_per_feed: int = 5,
    feeds: Optional[dict[str, str]] = None,
    topic: str = "",
    verbose: bool = True,
) -> list[RawSignal]:
    """
    Poll feeds and return all signals sorted newest-first.

    If *topic* is provided, feeds relevant to the topic's keywords are polled
    first so the LLM sees higher-quality signals at the top of its context.
    """
    if feeds is None:
        feeds = RSS_FEEDS

    # Re-order feed keys so topic-relevant ones come first
    ordered = _prioritise_feeds(list(feeds.keys()), topic)
    ordered_feeds = {k: feeds[k] for k in ordered if k in feeds}

    all_signals: list[RawSignal] = []

    if verbose:
        print(f"\n  [RSS SCRAPER] Polling {len(ordered_feeds)} feeds (max {max_per_feed} items each)…")

    for name, url in ordered_feeds.items():
        items = fetch_feed(name, url, max_per_feed)
        if verbose:
            status = f"{len(items)} signals" if items else "0 — skipped/failed"
            print(f"    {name:<16} {status}")
        all_signals.extend(items)

    # Sort newest first (None dates go to the end)
    all_signals.sort(
        key=lambda s: s.published or datetime.min,
        reverse=True,
    )

    if verbose:
        print(f"  [RSS SCRAPER] Total: {len(all_signals)} raw signals collected")

    return all_signals


def _prioritise_feeds(feed_keys: list[str], topic: str) -> list[str]:
    """Return feed keys reordered so topic-relevant feeds come first."""
    if not topic:
        return feed_keys

    topic_lower = topic.lower()
    priority: list[str] = []
    for keyword, preferred in _TOPIC_FEEDS.items():
        if keyword in topic_lower:
            for f in preferred:
                if f not in priority and f in feed_keys:
                    priority.append(f)

    rest = [f for f in feed_keys if f not in priority]
    return priority + rest


# ── Standalone smoke-test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    signals = scrape_all(max_per_feed=3, verbose=True)
    print()
    for s in signals[:8]:
        ts = s.published.strftime("%Y-%m-%d") if s.published else "no date"
        print(f"  [{ts}] {s.short()}")
        if s.text:
            print(f"         {s.text[:120]}…")
        print()
