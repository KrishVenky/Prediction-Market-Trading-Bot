"""
Confidence Scorer  (Gemini Flash → Ollama fallback)
-----------------------------------------------------
Given parsed signals + the CrewAI debate output, produces a calibrated
probability and market edge estimate.

Uses llm_router.invoke_with_fallback so Gemini rate-limits automatically
switch to the local Ollama model.
"""

from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from src.llm_router import invoke_with_fallback

load_dotenv()


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(
    topic: str,
    parsed_signals: list[dict],
    debate_result: dict,
) -> str:
    signals_block = "\n".join(
        f"  • [{s.get('source','?').upper()}] {s.get('signal', s.get('title',''))}"
        f"  ({s.get('sentiment','neutral')})"
        for s in parsed_signals[:5]
    )

    bull  = debate_result.get("bull", "No bull argument provided.")[:400]
    bear  = debate_result.get("bear", "No bear argument provided.")[:400]
    verdict = debate_result.get("verdict", "No verdict yet.")[:300]

    return f"""You are a quantitative prediction-market analyst with expertise in
probability calibration.

MARKET QUESTION
  {topic}

SIGNAL EVIDENCE
{signals_block}

DEBATE SUMMARY
  BULL CASE : {bull}
  BEAR CASE : {bear}
  ARBITER   : {verdict}

TASK
Based on the evidence and debate above, estimate:

1. CONFIDENCE   — your probability (0.00–1.00) that the YES outcome occurs
2. MARKET_PRICE — your best estimate of the current market-implied price
   (use 0.50 if completely unknown)
3. EDGE         — CONFIDENCE minus MARKET_PRICE (can be negative)
4. REASONING    — one sentence justifying the numbers

Respond ONLY in this exact format (no extra text):
CONFIDENCE: <number>
MARKET_PRICE: <number>
EDGE: <number>
REASONING: <text>
"""


# ── Response parser ────────────────────────────────────────────────────────────

def _parse_response(text: str, fallback_confidence: float = 0.5) -> dict:
    def _num(pattern: str) -> Optional[float]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    confidence   = _num(r"CONFIDENCE\s*:\s*([+-]?[0-9.]+)")
    market_price = _num(r"MARKET_PRICE\s*:\s*([+-]?[0-9.]+)")
    edge_raw     = _num(r"EDGE\s*:\s*([+-]?[0-9.]+)")
    reason_match = re.search(r"REASONING\s*:\s*(.+)", text, re.DOTALL | re.IGNORECASE)

    confidence   = max(0.0, min(1.0, confidence   or fallback_confidence))
    market_price = max(0.0, min(1.0, market_price or 0.5))
    edge         = edge_raw if edge_raw is not None else (confidence - market_price)
    edge         = max(-1.0, min(1.0, edge))
    reasoning    = reason_match.group(1).strip() if reason_match else ""

    return {
        "confidence":   round(confidence,   3),
        "market_price": round(market_price, 3),
        "edge":         round(edge,         3),
        "reasoning":    reasoning,
    }


# Fix missing import
from typing import Optional


# ── Public API ─────────────────────────────────────────────────────────────────

def score_confidence(
    topic: str,
    parsed_signals: list[dict],
    debate_result: dict,
) -> dict:
    """
    Call Gemini Flash to produce confidence + edge scores.

    Returns:
        {
          "confidence":   float,   # 0–1
          "market_price": float,   # 0–1
          "edge":         float,   # −1 to +1
          "reasoning":    str,
        }
    """
    prompt = _build_prompt(topic, parsed_signals, debate_result)

    try:
        raw = invoke_with_fallback([HumanMessage(content=prompt)], temperature=0.2)
        print(f"\n  [SCORER — LLM RESPONSE]")
        print(f"  {raw.strip()}")
        return _parse_response(raw)
    except Exception as exc:
        print(f"  [SCORER] All LLMs failed: {exc}")
        return _fallback_score(parsed_signals, debate_result)


def _fallback_score(parsed_signals: list[dict], debate_result: dict) -> dict:
    """Rule-based fallback when Gemini is unavailable."""
    bullish = sum(1 for s in parsed_signals if s.get("sentiment") == "bullish")
    bearish = sum(1 for s in parsed_signals if s.get("sentiment") == "bearish")
    total   = max(1, bullish + bearish)

    position = debate_result.get("position", "ABSTAIN")
    base = bullish / total

    if position == "YES":
        confidence = 0.5 + base * 0.3
    elif position == "NO":
        confidence = 0.5 - (1 - base) * 0.3
    else:
        confidence = 0.5

    return {
        "confidence":   round(confidence,       3),
        "market_price": 0.5,
        "edge":         round(confidence - 0.5, 3),
        "reasoning":    "Rule-based fallback (Gemini unavailable).",
    }
