"""
PolySignal  —  TOML Inter-Agent Message Format
------------------------------------------------
Every pipeline node serialises its output as a structured TOML message
that is:
  1. Stored in the DB (agent_messages table)
  2. Emitted over SSE so the frontend can show the raw structured data
  3. Indexed into ChromaDB alongside the signals

TOML was chosen because:
  - Human-readable like JSON but with typed scalars and inline tables
  - Maps cleanly to Python dicts via tomllib/tomli_w
  - Great for showing "inter-agent contract" in a demo

Message types:
  fetch_complete   — raw signal batch from scraper
  parse_complete   — LLM-parsed signals with trust scores
  debate_complete  — Bull/Bear/Arbiter structured output
  score_complete   — Final confidence, market price, edge
"""

from __future__ import annotations

try:
    import tomllib      # stdlib Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # pip install tomli (3.10 backport)
import tomli_w          # pip install tomli-w
from datetime import datetime, timezone
from typing import Any


# ── Serialise ──────────────────────────────────────────────────────────────────

def build_fetch_message(run_id: str, topic: str, signals: list) -> str:
    """TOML message after node_fetch completes."""
    sources: dict[str, int] = {}
    for s in signals:
        sources[s.source] = sources.get(s.source, 0) + 1

    doc: dict[str, Any] = {
        "message": {
            "type":      "fetch_complete",
            "run_id":    run_id,
            "timestamp": _now(),
            "node":      "fetch",
        },
        "query": {
            "topic":        topic,
            "feeds_polled": len(sources),
            "total_signals": len(signals),
        },
        "sources": {
            src: {"count": cnt, "trust": _source_trust(src)}
            for src, cnt in sorted(sources.items(), key=lambda x: -x[1])
        },
    }
    return tomli_w.dumps(doc)


def build_parse_message(run_id: str, topic: str, parsed: list[dict], model_used: str) -> str:
    """TOML message after node_parse completes."""
    doc: dict[str, Any] = {
        "message": {
            "type":      "parse_complete",
            "run_id":    run_id,
            "timestamp": _now(),
            "node":      "parse",
            "llm_model": model_used,
        },
        "query": {"topic": topic},
        "signals": {
            f"signal_{i+1}": {
                "source":      s.get("source", ""),
                "title":       s.get("title", "")[:120],
                "sentiment":   s.get("sentiment", "neutral"),
                "relevance":   float(s.get("relevance", 5.0)),
                "trust_score": float(s.get("trust_score", 0.0)),
                "signal":      s.get("signal", "")[:200],
            }
            for i, s in enumerate(parsed[:6])
        },
        "stats": {
            "signals_parsed":  len(parsed),
            "avg_trust":       round(sum(s.get("trust_score", 0) for s in parsed) / max(len(parsed), 1), 3),
            "bullish_count":   sum(1 for s in parsed if s.get("sentiment") == "bullish"),
            "bearish_count":   sum(1 for s in parsed if s.get("sentiment") == "bearish"),
            "neutral_count":   sum(1 for s in parsed if s.get("sentiment") == "neutral"),
        },
    }
    return tomli_w.dumps(doc)


def build_debate_message(run_id: str, topic: str, result: dict, model_used: str) -> str:
    """TOML message after node_debate completes."""
    doc: dict[str, Any] = {
        "message": {
            "type":      "debate_complete",
            "run_id":    run_id,
            "timestamp": _now(),
            "node":      "debate",
            "llm_model": model_used,
        },
        "market": {
            "question": topic,
            "position": result.get("position", "ABSTAIN"),
        },
        "agents": {
            "bull": {
                "role":     "Bull Analyst",
                "argument": result.get("bull", "")[:300],
            },
            "bear": {
                "role":     "Bear Analyst",
                "argument": result.get("bear", "")[:300],
            },
            "arbiter": {
                "role":    "Arbiter (Superforecaster)",
                "verdict": result.get("verdict", "")[:300],
                "position": result.get("position", "ABSTAIN"),
            },
        },
    }
    return tomli_w.dumps(doc)


def build_score_message(run_id: str, topic: str, scores: dict, model_used: str) -> str:
    """TOML message after node_score completes."""
    doc: dict[str, Any] = {
        "message": {
            "type":      "score_complete",
            "run_id":    run_id,
            "timestamp": _now(),
            "node":      "score",
            "llm_model": model_used,
        },
        "market": {
            "question":     topic,
            "position":     scores.get("position", "ABSTAIN"),
        },
        "forecast": {
            "confidence":   float(scores.get("confidence",   0.5)),
            "market_price": float(scores.get("market_price", 0.5)),
            "edge":         float(scores.get("edge",         0.0)),
            "reasoning":    scores.get("reasoning", "")[:300],
        },
        "interpretation": {
            "edge_label": _edge_label(scores.get("edge", 0)),
            "signal":     _edge_signal(scores.get("edge", 0), scores.get("position", "ABSTAIN")),
        },
    }
    return tomli_w.dumps(doc)


# ── Deserialise ────────────────────────────────────────────────────────────────

def parse_toml(toml_str: str) -> dict:
    """Parse a TOML string back to a Python dict. Returns {} on error."""
    try:
        return tomllib.loads(toml_str)
    except Exception:
        return {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _edge_label(edge: float) -> str:
    if edge >= 0.15:  return "strong_buy"
    if edge >= 0.05:  return "buy"
    if edge >= -0.05: return "hold"
    if edge >= -0.15: return "sell"
    return "strong_sell"


def _edge_signal(edge: float, position: str) -> str:
    label = _edge_label(edge)
    return f"{position} — {label.replace('_', ' ').upper()}"


_SOURCE_TRUST = {
    "metaculus": 0.95, "reuters": 0.88, "reuters_finance": 0.88,
    "apnews": 0.86, "ft": 0.85, "bbc_world": 0.85, "bbc_politics": 0.85,
    "wsj_markets": 0.84, "wsj_economy": 0.84, "guardian_world": 0.82,
    "guardian_us": 0.82, "guardian_biz": 0.82, "npr_economy": 0.80,
    "politico": 0.78, "axios": 0.76, "the_hill": 0.72,
    "cnbc_economy": 0.73, "cnbc_markets": 0.73, "marketwatch": 0.70,
    "forexlive": 0.68, "seeking_alpha": 0.65, "zerohedge": 0.62,
    "coindesk": 0.68, "cointelegraph": 0.65, "decrypt": 0.63,
    "theblock": 0.65, "techcrunch": 0.68, "twitter_nitter": 0.60,
}

def _source_trust(source: str) -> float:
    return _SOURCE_TRUST.get(source, 0.55)
