"""Tests for src/forecasting/confidence_scorer.py — no real LLM calls."""
from __future__ import annotations

import pytest
from src.forecasting.confidence_scorer import _parse_response, _fallback_score


# ── _parse_response ────────────────────────────────────────────────────────────

GOOD_RESPONSE = """
CONFIDENCE: 0.72
MARKET_PRICE: 0.55
EDGE: 0.17
REASONING: Strong dovish Fed signals and cooling inflation data support YES.
"""

def test_parse_response_happy_path():
    result = _parse_response(GOOD_RESPONSE)
    assert result["confidence"]   == pytest.approx(0.72, abs=0.001)
    assert result["market_price"] == pytest.approx(0.55, abs=0.001)
    assert result["edge"]         == pytest.approx(0.17, abs=0.001)
    assert "dovish" in result["reasoning"]


def test_parse_response_clamps_confidence_above_one():
    result = _parse_response("CONFIDENCE: 1.5\nMARKET_PRICE: 0.5\nEDGE: 1.0\nREASONING: n/a")
    assert result["confidence"] == 1.0


def test_parse_response_clamps_confidence_below_zero():
    result = _parse_response("CONFIDENCE: -0.2\nMARKET_PRICE: 0.5\nEDGE: -0.7\nREASONING: n/a")
    assert result["confidence"] == 0.0


def test_parse_response_missing_fields_uses_fallback():
    result = _parse_response("Nothing useful here at all")
    # Should return something reasonable, not raise
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["edge"], float)


def test_parse_response_computes_edge_if_missing():
    result = _parse_response("CONFIDENCE: 0.65\nMARKET_PRICE: 0.50\nREASONING: test")
    # Edge not provided → computed as confidence − market_price
    assert result["edge"] == pytest.approx(0.65 - 0.50, abs=0.01)


def test_parse_response_negative_edge():
    result = _parse_response("CONFIDENCE: 0.40\nMARKET_PRICE: 0.60\nEDGE: -0.20\nREASONING: test")
    assert result["edge"] < 0


def test_parse_response_reasoning_extracted():
    txt = "CONFIDENCE: 0.6\nMARKET_PRICE: 0.5\nEDGE: 0.1\nREASONING: The market underestimates risk."
    result = _parse_response(txt)
    assert "underestimates" in result["reasoning"]


# ── _fallback_score ────────────────────────────────────────────────────────────

def test_fallback_score_all_bullish():
    signals = [{"sentiment": "bullish"}, {"sentiment": "bullish"}]
    result  = _fallback_score(signals, {"position": "YES"})
    assert result["confidence"] > 0.5


def test_fallback_score_all_bearish():
    signals = [{"sentiment": "bearish"}, {"sentiment": "bearish"}]
    result  = _fallback_score(signals, {"position": "NO"})
    assert result["confidence"] < 0.5


def test_fallback_score_abstain():
    signals = [{"sentiment": "neutral"}]
    result  = _fallback_score(signals, {"position": "ABSTAIN"})
    assert result["confidence"] == pytest.approx(0.5, abs=0.01)


def test_fallback_score_empty_signals():
    result = _fallback_score([], {"position": "ABSTAIN"})
    assert 0.0 <= result["confidence"] <= 1.0


def test_fallback_score_has_required_keys():
    result = _fallback_score([{"sentiment": "bullish"}], {"position": "YES"})
    assert {"confidence", "market_price", "edge", "reasoning"}.issubset(result.keys())
