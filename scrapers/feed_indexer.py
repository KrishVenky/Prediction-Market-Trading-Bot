"""
Feed Indexer  —  PolySignal
-----------------------------
Bulk-scrapes ALL configured RSS feeds and indexes every article directly
into ChromaDB — no LLM filtering, no topic restriction.

This gives the semantic search a comprehensive knowledge base so it can
find genuinely similar articles regardless of which pipeline runs have
been executed.

Usage:
  # One-shot bootstrap (run before demo):
  python scrapers/feed_indexer.py

  # Or call from code:
  from scrapers.feed_indexer import run_full_index
  stats = run_full_index()

Also incorporates Twitter/Nitter scraping for finance/politics accounts
when Nitter instances are reachable (graceful fallback if not).
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_ROOT / ".env")

from scrapers.rss_scraper import RSS_FEEDS, fetch_feed
from src.models import RawSignal
from storage.vector_store import index_raw_articles, collection_size

# ── Twitter/Nitter accounts to scrape (no API key needed) ─────────────────────
# These are high signal-to-noise public accounts for prediction markets.
# We try Nitter plain-requests; silently skip if all instances are down.

NITTER_ACCOUNTS = [
    "Markets",          # Bloomberg Markets
    "economics",        # Bloomberg Economics
    "ReutersMarkets",   # Reuters Markets
    "business",         # Bloomberg Business
    "PredictItMkts",    # PredictIt prediction market commentary
    "karaswisher",      # tech policy
    "federalreserve",   # Fed official account
]

# ── Feed scraping config ───────────────────────────────────────────────────────
MAX_PER_FEED    = 15   # articles per feed per refresh
MAX_WORKERS     = 6    # parallel feed fetches
MIN_TEXT_LEN    = 30   # skip stub articles with no body


def _article_id(url: str) -> str:
    """Stable, short ID for a URL — used as ChromaDB doc ID for deduplication."""
    return "rss_" + hashlib.md5(url.encode("utf-8", errors="replace")).hexdigest()[:16]


def _signal_to_article(s: RawSignal) -> dict:
    """Convert a RawSignal into a flat dict ready for vector_store.index_raw_articles."""
    return {
        "id":          _article_id(s.url),
        "url":         s.url,
        "title":       s.title,
        "text":        s.text,
        "source":      s.source,
        "published":   s.published.isoformat() if s.published else "",
        "tags":        ", ".join(s.tags),
        "sentiment":   "neutral",           # not LLM-classified yet
        "trust_score": _source_trust(s.source),
        "relevance":   5.0,                 # neutral — let vector search judge
        "signal_text": s.text[:300],
        "run_topic":   "",                  # not from a pipeline run
        "run_id":      "feed_index",
    }


def _source_trust(source: str) -> float:
    """Baseline trust by source (mirrors trust_score.py SOURCE_RELIABILITY)."""
    _trust = {
        "metaculus":       0.95,
        "reuters":         0.88,
        "reuters_finance": 0.88,
        "apnews":          0.86,
        "ft":              0.85,
        "bbc_world":       0.85,
        "bbc_politics":    0.85,
        "politico":        0.78,
        "axios":           0.76,
        "the_hill":        0.72,
        "npr_economy":     0.80,
        "cnbc_economy":    0.73,
        "cnbc_markets":    0.73,
        "marketwatch":     0.70,
        "coindesk":        0.68,
        "cointelegraph":   0.65,
        "decrypt":         0.63,
        "theblock":        0.65,
        "techcrunch":      0.68,
        "twitter_nitter":  0.60,
    }
    return _trust.get(source, 0.55)


# ── RSS bulk scrape ────────────────────────────────────────────────────────────

def scrape_all_feeds(max_per_feed: int = MAX_PER_FEED) -> list[dict]:
    """
    Fetch all RSS feeds in parallel and return a list of article dicts.
    Deduplicates by URL.
    """
    seen_urls: set[str] = set()
    articles: list[dict] = []

    print(f"  [INDEXER] Fetching {len(RSS_FEEDS)} feeds (max {max_per_feed} each, {MAX_WORKERS} workers)…")
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(fetch_feed, name, url, max_per_feed): name
            for name, url in RSS_FEEDS.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                signals = future.result()
                new_count = 0
                for s in signals:
                    if s.url in seen_urls:
                        continue
                    if len(s.text) < MIN_TEXT_LEN:
                        continue
                    seen_urls.add(s.url)
                    articles.append(_signal_to_article(s))
                    new_count += 1
                print(f"    {name:<18} {new_count} articles")
            except Exception as exc:
                print(f"    {name:<18} ERROR — {exc}")

    elapsed = time.time() - t0
    print(f"  [INDEXER] RSS done: {len(articles)} unique articles in {elapsed:.1f}s")
    return articles


# ── Twitter / Nitter scrape ────────────────────────────────────────────────────

def scrape_nitter_accounts(accounts: list[str] = NITTER_ACCOUNTS) -> list[dict]:
    """
    Attempt to scrape Twitter accounts via Nitter plain-requests.
    Returns an empty list silently if all Nitter instances are unreachable.
    """
    try:
        from scrapers.twitter_scraper import scrape_nitter_requests
    except ImportError:
        return []

    articles: list[dict] = []
    for username in accounts:
        try:
            profile = scrape_nitter_requests(username, max_tweets=10)
            if not profile:
                continue

            for tweet in profile.get("tweets", []):
                text = tweet.get("text", "").strip()
                url  = tweet.get("tweet_url", f"https://twitter.com/{username}")
                if not text or len(text) < MIN_TEXT_LEN:
                    continue

                articles.append({
                    "id":          _article_id(url),
                    "url":         url,
                    "title":       text[:120],
                    "text":        text,
                    "source":      "twitter_nitter",
                    "published":   tweet.get("time_display", ""),
                    "tags":        username,
                    "sentiment":   "neutral",
                    "trust_score": 0.60,
                    "relevance":   5.0,
                    "signal_text": text[:300],
                    "run_topic":   "",
                    "run_id":      "feed_index",
                })

            print(f"    @{username:<16} {len(articles)} tweets")
            time.sleep(0.5)   # polite delay between accounts

        except Exception as exc:
            print(f"    @{username:<16} skipped ({exc})")

    return articles


# ── Master index runner ────────────────────────────────────────────────────────

def run_full_index(
    max_per_feed: int = MAX_PER_FEED,
    include_twitter: bool = True,
) -> dict:
    """
    Scrape all feeds (+ optionally Twitter), index into ChromaDB.
    Returns a stats dict.
    """
    print(f"\n{'='*62}")
    print("  POLYSIGNAL  -  Feed Indexer")
    print(f"  Starting full index at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*62}")

    before = collection_size()

    # 1 — RSS feeds
    rss_articles = scrape_all_feeds(max_per_feed)

    # 2 — Twitter (optional, graceful skip)
    twitter_articles: list[dict] = []
    if include_twitter:
        print(f"\n  [INDEXER] Trying Nitter scrape for {len(NITTER_ACCOUNTS)} accounts…")
        twitter_articles = scrape_nitter_accounts()
        if not twitter_articles:
            print("  [INDEXER] Nitter unavailable — skipping Twitter (no impact on RSS index)")

    all_articles = rss_articles + twitter_articles
    print(f"\n  [INDEXER] Total to index: {len(all_articles)} articles")

    # 3 — Push to ChromaDB
    if all_articles:
        index_raw_articles(all_articles)

    after = collection_size()
    new   = after - before

    print(f"\n{'='*62}")
    print(f"  Index complete: {after} total  (+{new} new)")
    print(f"{'='*62}\n")

    return {
        "rss":     len(rss_articles),
        "twitter": len(twitter_articles),
        "total":   len(all_articles),
        "indexed_before": before,
        "indexed_after":  after,
        "new":     new,
    }


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    stats = run_full_index()
    print(f"Done. DB now has {stats['indexed_after']} signals.")
