"""
Trust Score
-----------
Computes a single 0–1 score for each parsed signal combining:
  • Source reliability  (40%) — how trustworthy is this feed?
  • LLM relevance       (40%) — how relevant did the parser rate it?
  • Recency             (20%) — how fresh is it?

Called at the end of node_parse so every parsed_signal dict carries
a `trust_score` key before hitting the DB or the UI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

# Calibrated per-source base scores
SOURCE_RELIABILITY: dict[str, float] = {
    "metaculus":    0.95,   # expert-curated prediction questions
    "bbc_world":    0.85,
    "bbc_politics": 0.85,
    "politico":     0.75,
    "coindesk":     0.65,
    "hn_frontpage": 0.55,
    "twitter":      0.45,
}
_DEFAULT_RELIABILITY = 0.55


def compute_trust(
    source: str,
    relevance: float,          # 0–10 from LLM
    published: Optional[datetime],
) -> float:
    """
    Returns a trust score in [0.0, 1.0] (3 decimal places).

    Recency decay: 1.0 if published < 1 h ago, linear decay to 0.2
    at 7 days (168 h), capped at 0.2 for older items.
    """
    source_score = SOURCE_RELIABILITY.get(source, _DEFAULT_RELIABILITY)
    relevance_score = max(0.0, min(1.0, relevance / 10.0))

    if published:
        age_hours = (datetime.utcnow() - published).total_seconds() / 3600.0
        recency_score = max(0.2, 1.0 - (age_hours / 168.0))
    else:
        recency_score = 0.4   # unknown age — penalise but don't ignore

    trust = (
        0.40 * source_score
        + 0.40 * relevance_score
        + 0.20 * recency_score
    )
    return round(min(1.0, max(0.0, trust)), 3)


def attach_trust_scores(
    parsed_signals: list[dict],
    raw_signals: list,          # list[RawSignal] — for published timestamps
) -> list[dict]:
    """
    Mutates each parsed_signal dict in-place, adding `trust_score`.
    Matches by list index (parsed_signals is a subset of the first N raws).
    Returns the same list for chaining.
    """
    # Build a url→published lookup from raw signals
    pub_lookup: dict[str, Optional[datetime]] = {
        r.url: r.published for r in raw_signals
    }

    for sig in parsed_signals:
        published = pub_lookup.get(sig.get("url"))
        sig["trust_score"] = compute_trust(
            source=sig.get("source", ""),
            relevance=sig.get("relevance", 5.0),
            published=published,
        )

    return parsed_signals
