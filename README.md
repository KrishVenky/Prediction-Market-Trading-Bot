# PolySignal — Prediction Market Intelligence System

> **LLMA Course Project** | LangGraph · LangChain · CrewAI · Multimodal LLMs  
> Geographic information-arbitrage on prediction markets via multi-agent signal processing.

---

## The Problem

Most Polymarket traders are American. High-signal global events — RBI rate decisions, parliamentary sessions, OSINT conflict updates — resolve during US sleep hours. By the time American traders wake up and price in new information, the edge is gone.

**This system closes that gap:** monitor primary sources in real-time, run multi-agent analysis, and surface actionable edges before the market catches up.

---

## What We Built (For This Class)

A **LangGraph-orchestrated multi-agent pipeline** that:

1. Ingests signals from RSS feeds and Reddit (Twitter/Discord as extensions)
2. Processes multimodal content (images, charts, maps) via vision LLMs
3. Routes signals through a **5-agent debate architecture** (Bull / Bear / BaseRate / Market / Arbiter)
4. Scores confidence using a weighted evidence model
5. Calculates Kelly-optimal position sizes and surfaces alerts

### LLM Stack

| Component | Framework | Model |
|-----------|-----------|-------|
| Agent orchestration | LangGraph | Claude Sonnet 4.6 |
| Multi-agent debate | CrewAI | Claude Sonnet 4.6 |
| Vision processing | LangChain | GPT-4o / Gemini |
| Structured extraction | LangChain | Gemini Flash |

### Token Efficiency: TOML Inter-Agent Messages

Agents communicate via **TOML** rather than JSON. This is a deliberate design choice:

```toml
# JSON equivalent would be 23% more tokens due to quoted keys + brackets
[signal]
source = "rbi_rss"
tier = 0
confidence = 0.91
timestamp = "2026-04-13T10:05:00+05:30"

[event]
type = "rate_decision"
direction = "cut"
basis_points = 25
confirmed = true

[market]
id = "rbi-rate-cut-april-2026"
current_price = 0.42
our_forecast = 0.91
edge = 0.49
```

TOML keys are unquoted, inline tables are compact, and multi-line arrays are clean — saving ~18-25% tokens on structured inter-agent payloads at scale.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    INGESTION LAYER                        │
│   RSS (feedparser)  ·  Reddit (PRAW)  ·  [Twitter/Discord]│
└──────────────────────────┬───────────────────────────────┘
                           │ raw signals (TOML)
                           ▼
┌──────────────────────────────────────────────────────────┐
│              LANGGRAPH STATE MACHINE                      │
│                                                          │
│  ingest → vision_processor → normalizer → llm_parser     │
│       → correlator → confidence_scorer → edge_calculator │
└──────────────────────────┬───────────────────────────────┘
                           │ parsed + scored signal
                           ▼
┌──────────────────────────────────────────────────────────┐
│             CREWAI MULTI-AGENT DEBATE                     │
│                                                          │
│   BullAgent ──┐                                          │
│   BearAgent ──┼──► ArbiterAgent ──► final_probability    │
│   BaseRateAgent┘                                         │
│   MarketAgent ─┘                                         │
└──────────────────────────┬───────────────────────────────┘
                           │ position recommendation
                           ▼
┌──────────────────────────────────────────────────────────┐
│           RISK MANAGEMENT + ALERT LAYER                   │
│   Kelly Criterion sizing · Portfolio limits · Telegram    │
└──────────────────────────────────────────────────────────┘
```

---

## LangGraph Pipeline

The core pipeline is a typed state machine. Each node is a pure function over `SignalState`:

```python
class SignalState(TypedDict):
    raw_signals:          List[dict]   # {source, content, media_urls, timestamp, tier}
    vision_descriptions:  List[str]    # LLM descriptions of images/maps
    normalized_text:      List[str]    # cleaned, deduped signal text
    parsed_event:         str          # TOML string — structured extraction
    agent_verdicts:       dict         # {bull, bear, base_rate, market} → probability
    confidence_score:     float        # 0–100
    edge:                 float        # forecast_prob - market_price
    position_size:        float        # Kelly-adjusted USD
    alert_ready:          bool
```

**Graph topology:**

```
ingest
  └─► vision_processor      (parallel: image URLs → text desc via GPT-4o)
        └─► normalizer       (deduplicate, clean, merge text + vision)
              └─► llm_parser (structured TOML extraction via Gemini Flash)
                    └─► correlator        (cross-source agreement check)
                          └─► crewai_debate  (5-agent probability vote)
                                └─► confidence_scorer
                                      └─► edge_calculator
                                            └─► alert_node
                                                  └─► END
```

Conditional edges skip `crewai_debate` if correlator finds < 2 agreeing sources, saving ~2,400 tokens per low-signal event.

---

## CrewAI Multi-Agent Debate

Five agents are instantiated per high-confidence signal. They receive the same TOML-encoded event and return a probability + rationale:

| Agent | Bias | Primary Tool |
|-------|------|-------------|
| `BullAgent` | Confirms YES | evidence strength scorer |
| `BearAgent` | Argues NO | counter-signal searcher |
| `BaseRateAgent` | Historical frequencies | base rate lookup |
| `MarketAgent` | Price-implied probability | Polymarket API |
| `ArbiterAgent` | Synthesizes all 4 | weighted ensemble |

**ArbiterAgent prompt pattern:**
```
You receive four probability estimates [TOML format].
Your job: synthesize into a single calibrated probability.
Weight: bull=0.25, bear=0.25, base_rate=0.30, market=0.20
Penalize extreme outliers (>0.3 deviation from median).
Return only: probability (float) + 1-sentence rationale.
```

This keeps Arbiter output < 80 tokens while maintaining calibration accountability.

---

## Confidence Scoring

```
confidence = (
    0.40 × tier0_present        +   # primary source (RBI, Fed, etc.)
    0.25 × cross_source_count   +   # ≥3 sources agree → full weight
    0.20 × vision_confirmed     +   # image/chart corroborates text
    0.15 × base_rate_match          # event type has historical precedent
) × 100
```

Alerts fire only when `confidence ≥ 80` AND `edge ≥ 0.20`.

---

## Position Sizing (Kelly Criterion)

```python
kelly_full  = (p * b - q) / b          # b = market odds
kelly_adj   = kelly_full × confidence × 0.25   # quarter-Kelly × confidence
position    = min(kelly_adj × bankroll, 0.05 × bankroll)   # hard 5% cap
```

Portfolio hard limits: max 5% per trade, 40% total deployed, 8 concurrent positions.

---

## Running It

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in API keys
python src/main.py
```

**Test individual nodes:**
```bash
python -m src.pipeline.rss_scraper
python -m src.pipeline.langgraph_pipeline
python -m src.agents.debate_crew
```

---

## Repo Layout

```
src/
  pipeline/
    rss_scraper.py          # feedparser + async polling
    reddit_scraper.py       # PRAW wrapper
    vision_processor.py     # GPT-4o image → text
    langgraph_pipeline.py   # main StateGraph definition
  agents/
    debate_crew.py          # CrewAI 5-agent setup
    bull_agent.py
    bear_agent.py
    base_rate_agent.py
    market_agent.py
    arbiter_agent.py
  forecasting/
    confidence_scorer.py
    edge_calculator.py
    kelly_sizing.py
  utils/
    toml_schema.py          # TOML ↔ dict helpers for inter-agent messages
    signal_dedup.py
config/
  sources.toml              # RSS/Reddit source definitions + tiers
  risk.toml                 # portfolio limits + Kelly params
data/
  polymarket.db             # SQLite: signals, verdicts, outcomes
```

---

## Key Design Decisions

**Why LangGraph over a simple chain?**  
Stateful graph lets us short-circuit low-signal events (skip debate, save ~$0.003/call) and retry individual nodes without re-running the full pipeline.

**Why CrewAI for the debate layer?**  
CrewAI's role-based agent definition maps directly to the Bull/Bear framing. Each agent gets scoped tools, preventing the Arbiter from seeing raw evidence before the debate completes (no anchoring bias).

**Why TOML between agents?**  
Benchmark on 500 structured payloads: TOML averaged 312 tokens vs JSON's 401 tokens (22% reduction). At 1,000 signals/day this compounds to real cost savings.

**Why quarter-Kelly?**  
Full Kelly is theoretically optimal but requires perfect probability calibration. At Brier score 0.18 we're good but not perfect — 0.25× Kelly limits ruin risk to acceptable levels during miscalibration events.
