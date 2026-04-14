"""Tests for src/models.py and src/trust_score.py"""
from __future__ import annotations

from datetime import datetime, timedelta
import pytest
from src.models import RawSignal, SignalState, empty_state
from src.trust_score import compute_trust, attach_trust_scores, SOURCE_RELIABILITY


# ── RawSignal ──────────────────────────────────────────────────────────────────

def test_raw_signal_creation(raw_signal):
    assert raw_signal.source == "bbc_world"
    assert raw_signal.title  == "Fed signals rate cut possible in Q2 2025"
    assert isinstance(raw_signal.tags, list)
    assert isinstance(raw_signal.metadata, dict)


def test_raw_signal_short_truncates(raw_signal):
    short = raw_signal.short()
    assert short.startswith("[BBC_WORLD]")
    assert len(short) <= 100


def test_raw_signal_defaults():
    s = RawSignal(source="test", url="http://x.com", title="T", text="body")
    assert s.published is None
    assert s.tags      == []
    assert s.metadata  == {}


# ── empty_state ────────────────────────────────────────────────────────────────

def test_empty_state_keys():
    s = empty_state("test topic")
    required = {"topic", "raw_signals", "parsed_signals",
                "debate_result", "confidence_score", "edge", "final_output"}
    assert required.issubset(s.keys())


def test_empty_state_defaults():
    s = empty_state("my question")
    assert s["topic"]           == "my question"
    assert s["raw_signals"]     == []
    assert s["parsed_signals"]  == []
    assert s["debate_result"]   == {}
    assert s["confidence_score"] == 0.0
    assert s["edge"]            == 0.0
    assert s["final_output"]    == ""


# ── compute_trust ──────────────────────────────────────────────────────────────

def test_trust_metaculus_high_relevance():
    score = compute_trust("metaculus", 9.0, datetime.utcnow() - timedelta(hours=1))
    assert score >= 0.80


def test_trust_unknown_source_uses_default():
    score = compute_trust("some_random_feed", 5.0, None)
    assert 0.0 < score < 1.0


def test_trust_old_item_penalised():
    fresh = compute_trust("bbc_world", 8.0, datetime.utcnow() - timedelta(hours=1))
    stale = compute_trust("bbc_world", 8.0, datetime.utcnow() - timedelta(days=10))
    assert fresh > stale


def test_trust_no_published_date():
    score = compute_trust("politico", 6.0, None)
    assert 0.0 < score < 1.0


def test_trust_bounds():
    for source in SOURCE_RELIABILITY:
        for rel in [0.0, 5.0, 10.0]:
            score = compute_trust(source, rel, datetime.utcnow())
            assert 0.0 <= score <= 1.0, f"{source} rel={rel} → {score}"


# ── attach_trust_scores ────────────────────────────────────────────────────────

def test_attach_trust_scores_adds_key(parsed_signal, raw_signal):
    sig = dict(parsed_signal)
    del sig["trust_score"]   # remove so we can test it gets added
    result = attach_trust_scores([sig], [raw_signal])
    assert "trust_score" in result[0]
    assert 0.0 <= result[0]["trust_score"] <= 1.0


def test_attach_trust_scores_returns_same_list(parsed_signal, raw_signal):
    sigs   = [dict(parsed_signal)]
    result = attach_trust_scores(sigs, [raw_signal])
    assert result is sigs   # mutates in-place, returns same list
