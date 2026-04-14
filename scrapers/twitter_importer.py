"""
PolySignal  —  Twitter JSON Importer
--------------------------------------
Imports tweets scraped by your friend (or any scraper) into ChromaDB
so they become searchable alongside RSS signals.

Expected input format (list of tweet objects, flexible):
  [
    {
      "username": "NickTimiraos",
      "text": "Fed officials signal...",
      "url": "https://twitter.com/NickTimiraos/status/123",
      "timestamp": "2025-04-14T10:30:00Z",   (or "time_display", "created_at")
      "likes": 1200,
      "retweets": 340
    },
    ...
  ]

Also accepts the output format from scrapers/twitter_scraper.py (nested
profiles with tweets arrays).

Usage:
  python scrapers/twitter_importer.py tweets.json
  python scrapers/twitter_importer.py tweets.json --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_ROOT / ".env")

from storage.vector_store import index_raw_articles, collection_size
import storage.db as db

# Trust score by account — add known accounts here
_ACCOUNT_TRUST = {
    "nicktimiraos":   0.92,   # WSJ Fed reporter — gold standard
    "federalreserve": 0.95,
    "markets":        0.85,   # Bloomberg Markets
    "economics":      0.84,   # Bloomberg Economics
    "reutersmarkets": 0.86,
    "business":       0.82,
    "wsjmarkets":     0.84,
    "predictitmarks": 0.72,
    "polymarkethq":   0.74,
    "kalshi":         0.73,
    "zerohedge":      0.62,
    "karaswisher":    0.75,
    "elonmusk":       0.60,
    "jimcramer":      0.45,   # inverse indicator :)
}

def _tweet_id(url: str) -> str:
    return "tw_" + hashlib.md5(url.encode("utf-8", errors="replace")).hexdigest()[:16]


def _account_trust(username: str) -> float:
    return _ACCOUNT_TRUST.get(username.lower().lstrip("@"), 0.60)


def _parse_timestamp(tweet: dict) -> str:
    for key in ("timestamp", "time_display", "created_at", "tweet_date"):
        val = tweet.get(key, "")
        if val:
            return str(val)[:30]
    return datetime.utcnow().isoformat() + "Z"


def _normalize_tweet(tweet: dict, username: str) -> dict | None:
    text = tweet.get("text", tweet.get("tweet_text", "")).strip()
    if not text or len(text) < 20:
        return None

    url = tweet.get("url", tweet.get("tweet_url", tweet.get("link", "")))
    if not url:
        url = f"https://twitter.com/{username}"

    return {
        "id":          _tweet_id(url),
        "url":         url,
        "title":       text[:120],
        "text":        text,
        "source":      "twitter",
        "source_type": "twitter",
        "published":   _parse_timestamp(tweet),
        "tags":        f"@{username}",
        "sentiment":   "neutral",
        "trust_score": _account_trust(username),
        "relevance":   6.0,
        "signal_text": text[:400],
        "run_topic":   "",
        "run_id":      "twitter_import",
        "likes":       int(tweet.get("likes", tweet.get("like_count", 0)) or 0),
        "retweets":    int(tweet.get("retweets", tweet.get("retweet_count", 0)) or 0),
    }


def load_tweets(path: Path) -> list[dict]:
    """
    Loads and normalises tweets from a JSON file.
    Handles both flat list and nested profile format.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    tweets: list[dict] = []

    # Flat list of tweet objects
    if isinstance(data, list) and data and "text" in data[0]:
        for tw in data:
            username = tw.get("username", tw.get("screen_name", "unknown"))
            norm = _normalize_tweet(tw, username)
            if norm:
                tweets.append(norm)

    # List of profile objects with nested tweets arrays
    elif isinstance(data, list):
        for profile in data:
            username = profile.get("username", profile.get("screen_name", "unknown"))
            for tw in profile.get("tweets", []):
                tw.setdefault("username", username)
                norm = _normalize_tweet(tw, username)
                if norm:
                    tweets.append(norm)

    # Single profile object
    elif isinstance(data, dict) and "tweets" in data:
        username = data.get("username", "unknown")
        for tw in data["tweets"]:
            norm = _normalize_tweet(tw, username)
            if norm:
                tweets.append(norm)

    return tweets


def import_tweets(path: Path, dry_run: bool = False) -> dict:
    print(f"\n  [TWITTER IMPORTER] Loading {path.name}…")
    tweets = load_tweets(path)
    print(f"  [TWITTER IMPORTER] Parsed {len(tweets)} tweets")

    if not tweets:
        print("  [TWITTER IMPORTER] Nothing to import.")
        return {"parsed": 0, "indexed": 0}

    # Show sample
    for tw in tweets[:3]:
        print(f"    @{tw['tags']:<20} trust={tw['trust_score']:.2f} | {tw['title'][:60]}")

    if dry_run:
        print(f"\n  [DRY RUN] Would index {len(tweets)} tweets — skipping.")
        return {"parsed": len(tweets), "indexed": 0}

    before = collection_size()
    index_raw_articles(tweets)
    after = collection_size()

    # Also register twitter as a source in the DB
    db.init_db()
    db.upsert_source("twitter", "twitter", "nitter/api", 0.70)
    db.increment_source_count("twitter", len(tweets))

    print(f"\n  [TWITTER IMPORTER] Done. ChromaDB: {before} -> {after} (+{after-before} new)")
    return {"parsed": len(tweets), "indexed": after - before}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import Twitter JSON into PolySignal ChromaDB")
    parser.add_argument("file", help="Path to JSON file with tweets")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't write to DB")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    stats = import_tweets(path, dry_run=args.dry_run)
    print(f"\n  Imported: {stats['indexed']} tweets into ChromaDB")
    print(f"  Total indexed: {collection_size()}")
