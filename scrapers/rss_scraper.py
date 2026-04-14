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
    # ── US Politics & Policy ──────────────────────────────────────────────────
    "politico":         "https://rss.politico.com/politics-news.xml",
    "bbc_politics":     "http://feeds.bbci.co.uk/news/politics/rss.xml",
    "the_hill":         "https://thehill.com/rss/syndicator/19109",
    "axios":            "https://api.axios.com/feed/",
    "guardian_us":      "https://www.theguardian.com/us-news/rss",

    # ── World / Macro News ────────────────────────────────────────────────────
    "bbc_world":        "http://feeds.bbci.co.uk/news/world/rss.xml",
    "guardian_world":   "https://www.theguardian.com/world/rss",

    # ── Economics & Markets ───────────────────────────────────────────────────
    "wsj_markets":      "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "wsj_economy":      "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
    "marketwatch":      "http://feeds.marketwatch.com/marketwatch/topstories/",
    "cnbc_economy":     "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "cnbc_markets":     "https://www.cnbc.com/id/15839135/device/rss/rss.html",
    "ft":               "https://www.ft.com/rss/home",
    "guardian_biz":     "https://www.theguardian.com/business/rss",
    "npr_economy":      "https://feeds.npr.org/1017/rss.xml",
    "forexlive":        "https://www.forexlive.com/feed",
    "seeking_alpha":    "https://seekingalpha.com/feed.xml",
    "zerohedge":        "https://feeds.feedburner.com/zerohedge/feed",

    # ── Crypto / DeFi ─────────────────────────────────────────────────────────
    "cointelegraph":    "https://cointelegraph.com/rss",
    "decrypt":          "https://decrypt.co/feed",
    "theblock":         "https://www.theblock.co/rss.xml",

    # ── Tech & Regulation ─────────────────────────────────────────────────────
    "techcrunch":       "https://techcrunch.com/feed/",
}

# Topic-keyword → feeds to PRIORITISE (others still run but these go first)
_TOPIC_FEEDS: dict[str, list[str]] = {
    "crypto":    ["cointelegraph", "decrypt", "theblock", "cnbc_markets"],
    "bitcoin":   ["cointelegraph", "decrypt", "theblock", "cnbc_markets"],
    "btc":       ["cointelegraph", "decrypt", "theblock", "cnbc_markets"],
    "ethereum":  ["cointelegraph", "decrypt", "theblock"],
    "defi":      ["cointelegraph", "decrypt", "theblock"],
    "fed":       ["wsj_markets", "cnbc_economy", "ft", "marketwatch", "forexlive", "politico"],
    "rate":      ["wsj_markets", "cnbc_economy", "ft", "marketwatch", "forexlive"],
    "interest":  ["wsj_markets", "cnbc_economy", "ft", "marketwatch", "forexlive"],
    "inflation": ["wsj_economy", "cnbc_economy", "ft", "marketwatch", "guardian_biz"],
    "recession": ["wsj_economy", "cnbc_markets", "ft", "guardian_biz", "politico"],
    "gdp":       ["wsj_economy", "cnbc_economy", "ft", "marketwatch", "seeking_alpha"],
    "tariff":    ["wsj_economy", "politico", "axios", "guardian_us", "cnbc_markets"],
    "trade":     ["wsj_economy", "politico", "axios", "ft", "guardian_biz"],
    "election":  ["politico", "axios", "guardian_us", "bbc_politics", "bbc_world"],
    "shutdown":  ["politico", "axios", "guardian_us", "bbc_politics"],
    "congress":  ["politico", "axios", "guardian_us", "bbc_politics"],
    "trump":     ["politico", "axios", "guardian_us", "bbc_politics", "wsj_economy"],
    "senate":    ["politico", "axios", "guardian_us", "bbc_politics"],
    "stock":     ["wsj_markets", "cnbc_markets", "seeking_alpha", "ft", "marketwatch"],
    "s&p":       ["wsj_markets", "cnbc_markets", "seeking_alpha", "ft"],
    "nasdaq":    ["wsj_markets", "cnbc_markets", "seeking_alpha"],
    "war":       ["bbc_world", "guardian_world", "axios", "the_hill"],
    "ai":        ["techcrunch", "wsj_economy", "axios", "guardian_biz"],
    "tech":      ["techcrunch", "wsj_economy", "axios"],
    "forex":     ["forexlive", "ft", "wsj_markets", "cnbc_markets"],
    "oil":       ["wsj_markets", "forexlive", "cnbc_markets", "ft"],
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

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def fetch_feed(name: str, url: str, max_items: int = 10) -> list[RawSignal]:
    """Fetch one RSS feed and return up to *max_items* RawSignals."""
    signals: list[RawSignal] = []
    try:
        feed = feedparser.parse(
            url,
            agent=_UA,
            request_headers={"Accept": "application/rss+xml,application/xml,text/xml,*/*"},
        )

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
