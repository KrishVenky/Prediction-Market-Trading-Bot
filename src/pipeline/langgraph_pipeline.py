"""
LangGraph Pipeline  —  PolySignal
-----------------------------------
StateGraph[SignalState] with 4 nodes:

  fetch  →  parse  →  debate  →  score  →  END

Each node emits SSE events via the module-level callback so the
FastAPI server can stream live progress to the browser.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Callable, Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pathlib import Path
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from src.llm_router import invoke_with_fallback
from src.models import SignalState, empty_state
from src.trust_score import attach_trust_scores

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

_DIVIDER = "─" * 62

# ── SSE event callback (set by api/server.py, None in CLI mode) ────────────────
_event_callback: Optional[Callable] = None


def set_event_callback(cb: Optional[Callable]) -> None:
    global _event_callback
    _event_callback = cb


def _emit(event_type: str, **payload) -> None:
    if _event_callback:
        try:
            _event_callback(event_type, payload)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — fetch
# ══════════════════════════════════════════════════════════════════════════════

def node_fetch(state: SignalState) -> dict:
    print(f"\n{_DIVIDER}")
    print("  NODE 1 / 4  •  FETCH  •  RSS scraper")
    print(_DIVIDER)

    _emit("node_start", node="fetch", message="Polling RSS feeds…")

    from scrapers.rss_scraper import scrape_all

    signals = scrape_all(max_per_feed=4, topic=state["topic"], verbose=True)

    _emit("node_done", node="fetch", count=len(signals),
          sources=list({s.source for s in signals}))

    return {"raw_signals": signals}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — parse  (Gemini Flash → Ollama fallback)
# ══════════════════════════════════════════════════════════════════════════════

def node_parse(state: SignalState) -> dict:
    print(f"\n{_DIVIDER}")
    print("  NODE 2 / 4  •  PARSE  •  Gemini Flash")
    print(_DIVIDER)

    _emit("node_start", node="parse", message="LLM analysing headlines…")

    topic       = state["topic"]
    raw_signals = state["raw_signals"]

    if not raw_signals:
        print("  [PARSE] No raw signals — skipping.")
        _emit("node_done", node="parse", count=0, signals=[])
        return {"parsed_signals": []}

    headlines = "\n".join(
        f"  {i+1}. [{s.source.upper()}] {s.title}"
        for i, s in enumerate(raw_signals[:12])
    )

    prompt = f"""You are a prediction-market intelligence analyst.

MARKET QUESTION
  {topic}

RAW HEADLINES (newest first)
{headlines}

TASK
Pick the 3 headlines most useful for forecasting the market question above.
For each, return exactly this block (no extra text):

SIGNAL <N>:
INDEX: <number from the list above>
EVENT: <specific outcome that could be bet on>
SENTIMENT: <bullish | bearish | neutral>
RELEVANCE: <0–10>
SIGNAL: <one sentence describing the market-moving implication>
---

Return only 3 SIGNAL blocks separated by ---.
"""

    print(f"  Topic    : {topic}")
    print(f"  Scanning : {min(12, len(raw_signals))} headlines…")

    try:
        raw_text = invoke_with_fallback([HumanMessage(content=prompt)], temperature=0.2)
        print(f"\n  [PARSER — LLM RESPONSE]")
        preview = raw_text[:500].replace("\n", "\n  ")
        print(f"  {preview}{'…' if len(raw_text) > 500 else ''}")
        parsed = _parse_signal_blocks(raw_text, raw_signals)
    except Exception as exc:
        print(f"  [PARSE] All LLMs failed: {exc} — using top-3 raw signals")
        parsed = _raw_to_parsed(raw_signals[:3])

    # Attach trust scores (needs raw signals for published timestamps)
    attach_trust_scores(parsed, raw_signals)

    print(f"\n  Parsed {len(parsed)} relevant signal(s)")
    for p in parsed:
        print(f"    [{p['source'].upper()}] {p['title'][:55]}  →  {p['sentiment'].upper()}  trust={p.get('trust_score',0):.2f}")

    _emit("node_done", node="parse", count=len(parsed),
          signals=[{
              "source":      s["source"],
              "url":         s["url"],
              "title":       s["title"],
              "sentiment":   s["sentiment"],
              "trust_score": s.get("trust_score", 0),
              "signal":      s["signal"],
          } for s in parsed])

    return {"parsed_signals": parsed}


def _parse_signal_blocks(text: str, raw_signals: list) -> list:
    blocks  = text.split("---")
    results = []

    for block in blocks:
        if "SIGNAL" not in block.upper():
            continue
        try:
            idx_m = re.search(r"INDEX\s*:\s*(\d+)",        block)
            evt_m = re.search(r"EVENT\s*:\s*(.+)",         block)
            snt_m = re.search(r"SENTIMENT\s*:\s*(\w+)",    block)
            rel_m = re.search(r"RELEVANCE\s*:\s*([\d.]+)", block)
            sig_m = re.search(r"SIGNAL\s*:\s*(.+)",        block, re.DOTALL)

            if not idx_m:
                continue

            idx = int(idx_m.group(1)) - 1
            raw = raw_signals[idx] if 0 <= idx < len(raw_signals) else raw_signals[0]

            results.append({
                "source":    raw.source,
                "url":       raw.url,
                "title":     raw.title,
                "event":     evt_m.group(1).strip() if evt_m else raw.title,
                "sentiment": snt_m.group(1).lower() if snt_m else "neutral",
                "relevance": float(rel_m.group(1)) if rel_m else 5.0,
                "signal":    sig_m.group(1).strip()[:200] if sig_m else raw.text[:150],
                "raw_text":  raw.text,
            })
        except Exception:
            continue

    # Drop anything the LLM rated below 4 — not relevant enough to show
    results = [r for r in results if r.get("relevance", 0) >= 4.0]
    return results[:3] if results else _raw_to_parsed(raw_signals[:3])


def _raw_to_parsed(raw_list: list) -> list:
    return [
        {
            "source":    r.source,
            "url":       r.url,
            "title":     r.title,
            "event":     r.title,
            "sentiment": "neutral",
            "relevance": 5.0,
            "signal":    r.text[:150],
            "raw_text":  r.text,
        }
        for r in raw_list
    ]


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — debate  (CrewAI)
# ══════════════════════════════════════════════════════════════════════════════

def node_debate(state: SignalState) -> dict:
    print(f"\n{_DIVIDER}")
    print("  NODE 3 / 4  •  DEBATE  •  CrewAI  (Bull / Bear / Arbiter)")
    print(_DIVIDER)

    _emit("node_start", node="debate", message="Bull / Bear / Arbiter agents debating…")

    from src.agents.debate_crew import run_debate

    parsed = state["parsed_signals"]
    topic  = state["topic"]

    if not parsed:
        fallback = {
            "bull":     "Insufficient signal data for a bullish case.",
            "bear":     "Insufficient signal data for a bearish case.",
            "verdict":  "ABSTAIN — no signals available.",
            "position": "ABSTAIN",
        }
        _emit("node_done", node="debate", position="ABSTAIN")
        return {"debate_result": fallback}

    context_signals = "\n".join(
        f"  • [{s['source'].upper()}] {s['signal']}" for s in parsed
    )
    debate_topic = f"{topic}\n\nContext summary: {parsed[0]['event']}"
    result = run_debate(debate_topic, context_signals)

    print(f"\n  POSITION : {result['position']}")
    print(f"  VERDICT  : {result['verdict'][:160]}…")

    _emit("node_done", node="debate",
          position=result["position"],
          verdict=result["verdict"][:200])

    return {"debate_result": result}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — score  (Gemini Flash) + final report
# ══════════════════════════════════════════════════════════════════════════════

def node_score(state: SignalState) -> dict:
    print(f"\n{_DIVIDER}")
    print("  NODE 4 / 4  •  SCORE  •  Gemini Flash")
    print(_DIVIDER)

    _emit("node_start", node="score", message="Computing confidence & edge…")

    from src.forecasting.confidence_scorer import score_confidence

    topic  = state["topic"]
    parsed = state["parsed_signals"]
    debate = state["debate_result"]

    scores   = score_confidence(topic, parsed, debate)
    conf     = scores["confidence"]
    edge     = scores["edge"]
    mkt      = scores["market_price"]
    position = debate.get("position", "ABSTAIN")
    reasoning = scores.get("reasoning", "")

    banner = "═" * 62
    report = f"""
{banner}
  POLYSIGNAL  ·  Intelligence Report
{banner}
  Question  : {topic}
{banner}
  POSITION  : {position}
  Confidence: {conf:.1%}   Market Price: {mkt:.1%}   Edge: {edge:+.1%}
  Reasoning : {reasoning[:120]}
{banner}
  BULL CASE
  {debate.get('bull', 'N/A')[:200]}

  BEAR CASE
  {debate.get('bear', 'N/A')[:200]}

  ARBITER VERDICT
  {debate.get('verdict', 'N/A')[:250]}
{banner}
  TOP SIGNALS"""

    for s in parsed:
        report += (
            f"\n  [{s['source'].upper():<12}] {s['title'][:55]}"
            f"\n               → {s['sentiment'].upper()}  "
            f"trust={s.get('trust_score',0):.2f}  |  {s['signal'][:65]}"
        )

    report += f"\n{banner}\n"
    print(report)

    _emit("node_done", node="score",
          confidence=conf, edge=edge, market_price=mkt,
          position=position, reasoning=reasoning,
          bull=debate.get("bull", "")[:300],
          bear=debate.get("bear", "")[:300],
          verdict=debate.get("verdict", "")[:300])

    return {
        "confidence_score": conf,
        "edge":             edge,
        "final_output":     report,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Graph builder
# ══════════════════════════════════════════════════════════════════════════════

def build_pipeline():
    builder = StateGraph(SignalState)
    builder.add_node("fetch",  node_fetch)
    builder.add_node("parse",  node_parse)
    builder.add_node("debate", node_debate)
    builder.add_node("score",  node_score)
    builder.set_entry_point("fetch")
    builder.add_edge("fetch",  "parse")
    builder.add_edge("parse",  "debate")
    builder.add_edge("debate", "score")
    builder.add_edge("score",  END)
    return builder.compile()


def run_pipeline(topic: str) -> SignalState:
    graph = build_pipeline()
    state = empty_state(topic)
    return graph.invoke(state)


if __name__ == "__main__":
    run_pipeline("Will the US Federal Reserve cut interest rates before July 2025?")
