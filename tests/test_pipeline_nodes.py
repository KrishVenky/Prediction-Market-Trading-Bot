"""
Tests for individual LangGraph nodes — all LLM and scraper calls are mocked.
We test node input→output contracts, not LLM quality.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from src.models import empty_state


# ── node_fetch ─────────────────────────────────────────────────────────────────

@patch("scrapers.rss_scraper.scrape_all")
def test_node_fetch_populates_raw_signals(mock_scrape, raw_signal):
    from src.pipeline.langgraph_pipeline import node_fetch

    mock_scrape.return_value = [raw_signal, raw_signal]
    state  = empty_state("test topic")
    result = node_fetch(state)

    assert "raw_signals" in result
    assert len(result["raw_signals"]) == 2


@patch("scrapers.rss_scraper.scrape_all")
def test_node_fetch_returns_empty_list_on_no_feeds(mock_scrape):
    from src.pipeline.langgraph_pipeline import node_fetch

    mock_scrape.return_value = []
    state  = empty_state("test topic")
    result = node_fetch(state)
    assert result["raw_signals"] == []


# ── node_parse ─────────────────────────────────────────────────────────────────

LLM_PARSE_RESPONSE = """
SIGNAL 1:
INDEX: 1
EVENT: Fed cuts rates before July 2025
SENTIMENT: bullish
RELEVANCE: 8
SIGNAL: Dovish Fed signals increase YES probability significantly.
---
"""


@patch("src.pipeline.langgraph_pipeline.invoke_with_fallback", return_value=LLM_PARSE_RESPONSE)
def test_node_parse_returns_parsed_signals(mock_llm, raw_signal):
    from src.pipeline.langgraph_pipeline import node_parse

    state = empty_state("Will the Fed cut rates?")
    state["raw_signals"] = [raw_signal]

    result = node_parse(state)

    assert "parsed_signals" in result
    assert len(result["parsed_signals"]) >= 1
    sig = result["parsed_signals"][0]
    assert "source"      in sig
    assert "sentiment"   in sig
    assert "trust_score" in sig


@patch("src.pipeline.langgraph_pipeline.invoke_with_fallback", return_value=LLM_PARSE_RESPONSE)
def test_node_parse_trust_scores_in_range(mock_llm, raw_signal):
    from src.pipeline.langgraph_pipeline import node_parse

    state = empty_state("test")
    state["raw_signals"] = [raw_signal]
    result = node_parse(state)
    for sig in result["parsed_signals"]:
        assert 0.0 <= sig["trust_score"] <= 1.0


@patch("src.pipeline.langgraph_pipeline.invoke_with_fallback",
       side_effect=RuntimeError("quota exceeded"))
def test_node_parse_falls_back_on_llm_error(mock_llm, raw_signal):
    from src.pipeline.langgraph_pipeline import node_parse

    state = empty_state("test")
    state["raw_signals"] = [raw_signal]
    result = node_parse(state)

    # Should not raise; uses raw signals as fallback
    assert "parsed_signals" in result
    assert len(result["parsed_signals"]) >= 1


def test_node_parse_empty_raw_signals_returns_empty():
    from src.pipeline.langgraph_pipeline import node_parse

    state = empty_state("test")
    state["raw_signals"] = []
    result = node_parse(state)
    assert result["parsed_signals"] == []


# ── node_debate ────────────────────────────────────────────────────────────────

@patch("src.agents.debate_crew.run_debate")
def test_node_debate_returns_debate_result(mock_debate, parsed_signal, debate_result):
    from src.pipeline.langgraph_pipeline import node_debate

    mock_debate.return_value = debate_result
    state = empty_state("test")
    state["parsed_signals"] = [parsed_signal]

    result = node_debate(state)
    assert "debate_result" in result
    assert result["debate_result"]["position"] in ("YES", "NO", "ABSTAIN")


def test_node_debate_no_signals_returns_abstain():
    from src.pipeline.langgraph_pipeline import node_debate

    state = empty_state("test")
    state["parsed_signals"] = []

    result = node_debate(state)
    assert result["debate_result"]["position"] == "ABSTAIN"


# ── node_score ─────────────────────────────────────────────────────────────────

MOCK_SCORE = {"confidence": 0.72, "market_price": 0.55, "edge": 0.17, "reasoning": "test"}


@patch("src.forecasting.confidence_scorer.score_confidence", return_value=MOCK_SCORE)
def test_node_score_populates_confidence(mock_score, base_state):
    from src.pipeline.langgraph_pipeline import node_score

    result = node_score(base_state)
    assert result["confidence_score"] == pytest.approx(0.72, abs=0.001)
    assert result["edge"]             == pytest.approx(0.17, abs=0.001)
    assert isinstance(result["final_output"], str)
    assert len(result["final_output"]) > 0


@patch("src.forecasting.confidence_scorer.score_confidence", return_value=MOCK_SCORE)
def test_node_score_report_contains_topic(mock_score, base_state):
    from src.pipeline.langgraph_pipeline import node_score

    result = node_score(base_state)
    assert "Fed" in result["final_output"]   # topic keyword present


# ── _parse_signal_blocks (unit) ────────────────────────────────────────────────

def test_parse_signal_blocks_parses_correctly(raw_signal):
    from src.pipeline.langgraph_pipeline import _parse_signal_blocks

    text = """
SIGNAL 1:
INDEX: 1
EVENT: Rate cut in Q2
SENTIMENT: bullish
RELEVANCE: 9
SIGNAL: Strong evidence for YES outcome.
---
"""
    result = _parse_signal_blocks(text, [raw_signal])
    assert len(result) == 1
    assert result[0]["sentiment"] == "bullish"
    assert result[0]["relevance"] == 9.0


def test_parse_signal_blocks_invalid_index_uses_first(raw_signal):
    from src.pipeline.langgraph_pipeline import _parse_signal_blocks

    text = "SIGNAL 1:\nINDEX: 999\nEVENT: x\nSENTIMENT: neutral\nRELEVANCE: 5\nSIGNAL: test\n---"
    result = _parse_signal_blocks(text, [raw_signal])
    assert result[0]["source"] == raw_signal.source   # fallback to first


def test_parse_signal_blocks_empty_text_returns_raw(raw_signal):
    from src.pipeline.langgraph_pipeline import _parse_signal_blocks

    result = _parse_signal_blocks("", [raw_signal])
    assert len(result) == 1
    assert result[0]["title"] == raw_signal.title
