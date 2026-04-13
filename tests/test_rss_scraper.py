"""Tests for scrapers/rss_scraper.py — all feedparser calls are mocked."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest

from scrapers.rss_scraper import _strip_html, _parse_date, fetch_feed, scrape_all


# ── _strip_html ────────────────────────────────────────────────────────────────

def test_strip_html_basic():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_entities():
    # &amp; should be decoded to & (not left as &amp;)
    # &lt;ticker&gt; decodes to the literal text <ticker> — that's correct BS4 behaviour
    result = _strip_html("<p>AT&amp;T &lt;ticker&gt;</p>")
    assert "&amp;" not in result
    assert "AT&T" in result   # entity decoded properly


def test_strip_html_empty():
    assert _strip_html("") == ""


def test_strip_html_no_tags():
    assert _strip_html("plain text") == "plain text"


# ── _parse_date ────────────────────────────────────────────────────────────────

def test_parse_date_valid():
    entry = MagicMock()
    entry.published_parsed = (2026, 4, 13, 10, 30, 0, 0, 0, 0)
    result = _parse_date(entry)
    assert isinstance(result, datetime)
    assert result.year  == 2026
    assert result.month == 4


def test_parse_date_missing():
    entry = MagicMock()
    del entry.published_parsed
    result = _parse_date(entry)
    assert result is None


def test_parse_date_none_value():
    entry = MagicMock()
    entry.published_parsed = None
    result = _parse_date(entry)
    assert result is None


# ── fetch_feed ─────────────────────────────────────────────────────────────────

def _make_entry(title="Test headline", summary="Body text", url="http://x.com/1"):
    entry = MagicMock()
    entry.title            = title
    entry.summary          = summary
    entry.link             = url
    entry.published_parsed = (2026, 4, 13, 10, 0, 0, 0, 0, 0)
    entry.tags             = []
    # Ensure hasattr checks work
    del entry.description
    del entry.content
    return entry


def _make_feed(entries):
    feed_obj = MagicMock()
    feed_obj.entries = entries
    feed_obj.bozo    = False
    feed_obj.feed    = MagicMock()
    feed_obj.feed.get = lambda k, d="": d
    return feed_obj


@patch("scrapers.rss_scraper.feedparser.parse")
def test_fetch_feed_returns_raw_signals(mock_parse):
    mock_parse.return_value = _make_feed([_make_entry() for _ in range(5)])
    signals = fetch_feed("bbc_world", "http://bbc.com/rss", max_items=3)
    assert len(signals) == 3
    assert signals[0].source == "bbc_world"
    assert signals[0].title  == "Test headline"


@patch("scrapers.rss_scraper.feedparser.parse")
def test_fetch_feed_strips_html_from_summary(mock_parse):
    mock_parse.return_value = _make_feed([_make_entry(summary="<p>Clean <b>text</b></p>")])
    signals = fetch_feed("test", "http://x.com/rss", max_items=1)
    assert "<p>" not in signals[0].text
    assert "Clean" in signals[0].text


@patch("scrapers.rss_scraper.feedparser.parse")
def test_fetch_feed_skips_empty_title(mock_parse):
    entries = [_make_entry(title=""), _make_entry(title="Valid")]
    mock_parse.return_value = _make_feed(entries)
    signals = fetch_feed("test", "http://x.com/rss", max_items=5)
    assert len(signals) == 1
    assert signals[0].title == "Valid"


@patch("scrapers.rss_scraper.feedparser.parse")
def test_fetch_feed_bozo_no_entries(mock_parse):
    feed = _make_feed([])
    feed.bozo = True
    mock_parse.return_value = feed
    signals = fetch_feed("test", "http://x.com/rss")
    assert signals == []


@patch("scrapers.rss_scraper.feedparser.parse")
def test_fetch_feed_network_error(mock_parse):
    mock_parse.side_effect = Exception("connection refused")
    signals = fetch_feed("test", "http://x.com/rss")
    assert signals == []


# ── scrape_all ─────────────────────────────────────────────────────────────────

@patch("scrapers.rss_scraper.fetch_feed")
def test_scrape_all_aggregates_feeds(mock_fetch, raw_signal):
    mock_fetch.return_value = [raw_signal]
    feeds = {"feed_a": "http://a.com/rss", "feed_b": "http://b.com/rss"}
    signals = scrape_all(max_per_feed=2, feeds=feeds, verbose=False)
    assert len(signals) == 2
    assert mock_fetch.call_count == 2


@patch("scrapers.rss_scraper.fetch_feed")
def test_scrape_all_sorts_newest_first(mock_fetch):
    from src.models import RawSignal
    old = RawSignal("src","u","t","b", published=datetime(2026,1,1))
    new = RawSignal("src","u","t","b", published=datetime(2026,4,13))
    mock_fetch.return_value = [old, new]
    signals = scrape_all(feeds={"x": "u"}, verbose=False)
    assert signals[0].published > signals[1].published
