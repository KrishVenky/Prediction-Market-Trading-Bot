"""
Shared data models for PolySignal.

All inter-module data flows through these types so each layer
stays independent of the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from typing_extensions import TypedDict


# ── Raw scraped signal ─────────────────────────────────────────────────────────

@dataclass
class RawSignal:
    """A single scraped item before any LLM processing."""
    source: str          # 'rss_metaculus', 'rss_bbc', 'twitter', …
    url: str
    title: str
    text: str            # body / summary, HTML-stripped
    published: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def short(self) -> str:
        return f"[{self.source.upper()}] {self.title[:80]}"


# ── LangGraph pipeline state ───────────────────────────────────────────────────

class SignalState(TypedDict):
    """Mutable state carried through every LangGraph node."""
    topic: str                        # prediction market question being analyzed
    raw_signals: list[Any]            # list[RawSignal]
    parsed_signals: list[Any]         # list[dict] after Gemini parsing
    debate_result: dict               # bull/bear/arbiter outputs
    confidence_score: float           # 0.0 – 1.0
    edge: float                       # confidence − implied market price
    final_output: str                 # formatted console report


# ── Convenience defaults ───────────────────────────────────────────────────────

def empty_state(topic: str) -> SignalState:
    return SignalState(
        topic=topic,
        raw_signals=[],
        parsed_signals=[],
        debate_result={},
        confidence_score=0.0,
        edge=0.0,
        final_output="",
    )
