"""Shared pytest fixtures."""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime
import pytest
from src.models import RawSignal, empty_state


@pytest.fixture
def raw_signal():
    return RawSignal(
        source="bbc_world",
        url="https://bbc.com/news/test-123",
        title="Fed signals rate cut possible in Q2 2025",
        text="The Federal Reserve hinted at a possible rate cut amid cooling inflation data.",
        published=datetime(2026, 4, 13, 10, 0, 0),
        tags=["economics", "fed"],
        metadata={"feed_url": "https://bbc.com/rss"},
    )


@pytest.fixture
def parsed_signal():
    return {
        "source":      "bbc_world",
        "url":         "https://bbc.com/news/test-123",
        "title":       "Fed signals rate cut possible in Q2 2025",
        "event":       "Federal Reserve cuts rates before July 2025",
        "sentiment":   "bullish",
        "relevance":   8.0,
        "signal":      "Fed dovish signals increase YES probability.",
        "raw_text":    "The Federal Reserve hinted at a possible rate cut.",
        "trust_score": 0.82,
    }


@pytest.fixture
def debate_result():
    return {
        "bull":     "Inflation cooling trend strongly supports YES outcome.",
        "bear":     "Labour market resilience gives Fed cover to stay on hold.",
        "verdict":  "Balance of evidence slightly favours YES but uncertainty remains.",
        "position": "YES",
    }


@pytest.fixture
def base_state(raw_signal, parsed_signal, debate_result):
    s = empty_state("Will the Fed cut rates before July 2025?")
    s["raw_signals"]    = [raw_signal]
    s["parsed_signals"] = [parsed_signal]
    s["debate_result"]  = debate_result
    return s
