# System Architecture — PolySignal

Technical reference for the full pipeline. This document covers data flow, state schema, agent contracts, TOML message format, and operational constraints.

---

## 1. System Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│  EXTERNAL SOURCES                                               │
│  RBI RSS · Fed RSS · LiveMint · Reddit (PRAW) · [Twitter API]  │
│  [Discord bots] · [OSINT Twitter OSINT accounts]               │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP / WebSocket
┌──────────────────────────────▼──────────────────────────────────┐
│  INGESTION LAYER              (src/pipeline/)                   │
│  rss_scraper.py · reddit_scraper.py · [twitter_poller.py]      │
│  Outputs: List[RawSignal]  ──►  Redis queue (signals:raw)      │
└──────────────────────────────┬──────────────────────────────────┘
                               │ Redis pub/sub
┌──────────────────────────────▼──────────────────────────────────┐
│  LANGGRAPH PIPELINE           (src/pipeline/langgraph_pipeline) │
│  StateGraph[SignalState]                                        │
│  Nodes: ingest → vision → normalize → parse → correlate        │
│         → [crewai_debate] → score → size → alert               │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  CREWAI DEBATE LAYER          (src/agents/)                     │
│  5 agents · TOML message bus · ArbiterAgent synthesis           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  RISK + OUTPUT LAYER          (src/forecasting/)                │
│  Kelly sizing · Portfolio manager · Alert dispatcher            │
│  SQLite (signals, verdicts, outcomes) · Telegram [optional]     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Data Models

### 2.1 RawSignal (ingestion output)

```python
@dataclass
class RawSignal:
    id:         str          # sha256(source + content[:64])
    source:     str          # "rbi_rss" | "reddit_indiainv" | ...
    tier:       int          # 0 (critical) | 1 (high) | 2 (context)
    content:    str          # raw text
    media_urls: list[str]    # image/chart URLs (may be empty)
    timestamp:  datetime     # UTC
    category:   str          # "indian_policy" | "fed" | "crypto" | "geo"
```

### 2.2 SignalState (LangGraph shared state)

```python
class SignalState(TypedDict):
    # Ingestion
    raw_signals:          List[RawSignal]
    
    # Vision processing
    vision_descriptions:  List[str]        # "VISUAL_FROM_<source>: ..."
    
    # Normalization
    normalized_text:      List[str]        # cleaned, deduped
    
    # LLM extraction (TOML string)
    parsed_event:         str
    
    # Correlation
    source_count:         int
    agreement_score:      float            # 0–1
    skip_debate:          bool             # True if < 2 sources agree
    
    # CrewAI debate outputs
    agent_verdicts:       dict             # {agent_name: float}
    arbiter_probability:  float
    arbiter_rationale:    str
    
    # Scoring + sizing
    confidence_score:     float            # 0–100
    edge:                 float
    position_size:        float
    
    # Control
    alert_ready:          bool
    error:                Optional[str]
```

### 2.3 TOML Inter-Agent Message Schema

All structured data passed between agents uses TOML. This is the canonical schema:

```toml
# ParsedEvent — output of llm_parser node, input to all debate agents

[signal]
source    = "rbi_rss"          # string
tier      = 0                  # int: 0|1|2
timestamp = "2026-04-13T10:05:00+05:30"   # ISO 8601

[event]
type      = "rate_decision"    # "rate_decision"|"election"|"conflict"|"listing"|...
subject   = "RBI"
direction = "cut"              # "cut"|"hike"|"hold"|"win"|"lose"|"yes"|"no"
magnitude = 25                 # basis points, votes, etc. (nullable)
confirmed = true               # bool: direct primary source vs inferred

[evidence]
source_count   = 3
tier0_present  = true
vision_count   = 1
vision_summary = "Chart showing repo rate decreasing 25bps, titled 'MPC Decision Apr 2026'"

[market]
id            = "rbi-rate-cut-april-2026"
current_price = 0.42
volume_24h    = 15000          # USD
```

**Why TOML over JSON for LLM payloads:**
- `current_price = 0.42` vs `"current_price": 0.42` — no quotes on keys
- Arrays: `tags = ["policy", "india"]` vs `"tags": ["policy", "india"]`
- Avg savings across 500 structured payloads: **22% fewer tokens**
- Parseable in Python with stdlib `tomllib` (3.11+) — zero dependencies

---

## 3. LangGraph Node Specifications

### 3.1 Node Contract

Every node is a pure function:
```python
def node_name(state: SignalState) -> SignalState:
    ...
    return state   # always return full state (LangGraph requirement)
```

### 3.2 Node Inventory

| Node | Input fields | Output fields | LLM | Approx tokens |
|------|-------------|--------------|-----|---------------|
| `ingest` | — | `raw_signals` | none | 0 |
| `vision_processor` | `raw_signals` | `vision_descriptions` | GPT-4o | ~800/image |
| `normalizer` | `raw_signals`, `vision_descriptions` | `normalized_text` | none | 0 |
| `llm_parser` | `normalized_text` | `parsed_event` (TOML) | Gemini Flash | ~400 |
| `correlator` | `raw_signals`, `parsed_event` | `source_count`, `agreement_score`, `skip_debate` | none | 0 |
| `crewai_debate` | `parsed_event` | `agent_verdicts`, `arbiter_probability`, `arbiter_rationale` | Claude Sonnet | ~1,200 |
| `confidence_scorer` | `raw_signals`, `agent_verdicts`, `vision_descriptions` | `confidence_score` | Claude Sonnet | ~300 |
| `edge_calculator` | `confidence_score`, `arbiter_probability`, `parsed_event` | `edge`, `position_size` | none | 0 |
| `alert_node` | all | `alert_ready` | none | 0 |

### 3.3 Conditional Edge: skip_debate

```python
def should_debate(state: SignalState) -> str:
    if state["skip_debate"] or state["source_count"] < 2:
        return "confidence_scorer"   # skip CrewAI, save ~1,200 tokens
    return "crewai_debate"

graph.add_conditional_edges("correlator", should_debate, {
    "crewai_debate":    "crewai_debate",
    "confidence_scorer": "confidence_scorer",
})
```

---

## 4. CrewAI Agent Specifications

### 4.1 Agent Definitions

```python
from crewai import Agent, Task, Crew

bull_agent = Agent(
    role="Bull Analyst",
    goal="Find strongest YES evidence in the signal. Assign probability the event resolves YES.",
    backstory="You are an optimist who weights primary sources heavily and trusts confirmations.",
    llm=claude_sonnet,
    tools=[evidence_scorer_tool],
    max_iter=2,
    verbose=False,
)

bear_agent = Agent(
    role="Bear Analyst",
    goal="Find counter-signals and reasons the event may NOT resolve as reported.",
    backstory="You are a skeptic. You look for hedged language, unverified sources, and prior false alarms.",
    llm=claude_sonnet,
    tools=[counter_signal_tool],
    max_iter=2,
    verbose=False,
)

base_rate_agent = Agent(
    role="Base Rate Statistician",
    goal="Return the historical base rate for this event type. Ignore current signal content.",
    backstory="You only look at historical frequencies. You are immune to narrative bias.",
    llm=gemini_flash,
    tools=[base_rate_lookup_tool],
    max_iter=1,
    verbose=False,
)

market_agent = Agent(
    role="Market Analyst",
    goal="Report the market-implied probability and flag any anomalies (thin volume, stale price).",
    backstory="You read Polymarket prices and order books. You note whether price reflects the signal.",
    llm=gemini_flash,
    tools=[polymarket_price_tool],
    max_iter=1,
    verbose=False,
)

arbiter_agent = Agent(
    role="Arbiter",
    goal="Synthesize all four estimates into one calibrated probability. Output probability + 1 sentence.",
    backstory="""
        You receive four probability estimates in TOML format.
        Weight: bull=0.25, bear=0.25, base_rate=0.30, market=0.20.
        Penalize estimates >0.3 from the median (outlier penalty: halve their weight).
        Return ONLY: a float (0.0–1.0) and one sentence explaining the key deciding factor.
    """,
    llm=claude_sonnet,
    tools=[],
    max_iter=1,
    verbose=False,
)
```

### 4.2 Task + Crew Wiring

```python
def run_debate(parsed_event_toml: str) -> tuple[float, str]:
    bull_task       = Task(description=f"Analyze:\n{parsed_event_toml}", agent=bull_agent,       expected_output="probability: float")
    bear_task       = Task(description=f"Analyze:\n{parsed_event_toml}", agent=bear_agent,       expected_output="probability: float")
    base_rate_task  = Task(description=f"Event type: {event_type}",      agent=base_rate_agent,  expected_output="base_rate: float")
    market_task     = Task(description=f"Market id: {market_id}",        agent=market_agent,     expected_output="market_price: float")

    arbiter_task = Task(
        description="Synthesize the four estimates below (TOML):\n{verdicts_toml}",
        agent=arbiter_agent,
        expected_output="probability: float\nrationale: string",
        context=[bull_task, bear_task, base_rate_task, market_task],
    )

    crew = Crew(
        agents=[bull_agent, bear_agent, base_rate_agent, market_agent, arbiter_agent],
        tasks=[bull_task, bear_task, base_rate_task, market_task, arbiter_task],
        process=Process.sequential,   # Arbiter must run last
    )
    result = crew.kickoff()
    return parse_arbiter_output(result)
```

---

## 5. Confidence Scoring

```python
def confidence_scorer_node(state: SignalState) -> SignalState:
    tier0      = 0.40 if any_tier0(state["raw_signals"]) else 0.0
    cross_src  = 0.25 if state["source_count"] >= 3 else (0.10 if state["source_count"] == 2 else 0.0)
    vision     = 0.20 if state["vision_descriptions"] else 0.0
    base_rate  = get_base_rate_weight(state["parsed_event"])  # 0.0–0.15

    component_score = (tier0 + cross_src + vision + base_rate) * 100

    # LLM calibration pass (cheap — Gemini Flash)
    llm_score = float(gemini_flash.invoke(calibration_prompt(state, component_score)).content.strip())

    # Final: blend components (70%) with LLM judgment (30%)
    state["confidence_score"] = 0.70 * component_score + 0.30 * llm_score
    return state
```

---

## 6. Risk Management

### 6.1 Kelly Position Sizing

```python
def kelly_position(
    p: float,           # our forecast probability
    market_price: float,
    bankroll: float,
    confidence: float,  # 0–1 (confidence_score / 100)
) -> dict:
    b   = (1 - market_price) / market_price   # decimal odds
    q   = 1 - p
    k   = (p * b - q) / b                     # full Kelly
    adj = k * confidence * 0.25               # quarter-Kelly × confidence

    size = min(adj * bankroll, 0.05 * bankroll)  # hard 5% cap
    ev   = size * (p * b - q)

    return {"size": size, "kelly_raw": k, "kelly_adj": adj, "ev": ev, "edge": p - market_price}
```

### 6.2 Portfolio Limits (config/risk.toml)

```toml
[portfolio]
max_position_pct  = 0.05   # 5% per trade
max_deployed_pct  = 0.40   # 40% total
max_positions     = 8
min_edge          = 0.15
min_confidence    = 80

[kelly]
fraction          = 0.25
use_confidence    = true   # multiply kelly by (confidence/100)

[alerts]
min_confidence    = 80
min_edge          = 0.20
```

---

## 7. Source Tiers (config/sources.toml)

```toml
[rss.tier0]
  [[rss.tier0.feeds]]
  url      = "https://rbi.org.in/pressreleases_rss.xml"
  category = "indian_policy"
  poll_sec = 60

  [[rss.tier0.feeds]]
  url      = "https://www.federalreserve.gov/feeds/press_monetary.xml"
  category = "fed"
  poll_sec = 60

[rss.tier1]
  [[rss.tier1.feeds]]
  url      = "https://www.coindesk.com/arc/outboundfeeds/rss/"
  category = "crypto"
  poll_sec = 120

[reddit]
  [[reddit.subreddits]]
  name      = "IndiaInvestments"
  priority  = "high"
  poll_sec  = 300
  flair     = ["News", "Policy", "RBI"]

  [[reddit.subreddits]]
  name      = "CredibleDefense"
  priority  = "high"
  poll_sec  = 300
```

---

## 8. Database Schema (SQLite)

```sql
CREATE TABLE signals (
    id          TEXT PRIMARY KEY,
    source      TEXT,
    tier        INTEGER,
    content     TEXT,
    category    TEXT,
    timestamp   TEXT,
    processed   INTEGER DEFAULT 0
);

CREATE TABLE verdicts (
    signal_id       TEXT REFERENCES signals(id),
    bull_prob       REAL,
    bear_prob       REAL,
    base_rate_prob  REAL,
    market_price    REAL,
    arbiter_prob    REAL,
    confidence      REAL,
    edge            REAL,
    position_size   REAL,
    created_at      TEXT
);

CREATE TABLE outcomes (
    signal_id       TEXT REFERENCES signals(id),
    resolved_yes    INTEGER,    -- 1=YES, 0=NO
    pnl             REAL,
    resolved_at     TEXT
);
```

---

## 9. Operational Notes

**Rate limits:**
- Reddit PRAW: 60 req/min (OAuth) — stay under 30 with backoff
- Gemini Flash: 1,500 RPM free tier — sufficient for 500 signals/day
- GPT-4o vision: $0.01/image — skip non-OSINT images to control cost

**Deduplication:**
- Signal ID = `sha256(source + content[:64])` — skip if already in DB
- TTL on Redis queue: 1 hour — prevents reprocessing stale events

**Cold start:**
- On first run, seed `data/base_rates.toml` with historical event frequencies
- Without base rates, `base_rate_agent` returns 0.50 (neutral) — confidence score degrades gracefully

**LLM cost budget (per day, ~500 signals):**
- Vision (GPT-4o): ~50 images × $0.01 = $0.50
- LLM parser (Gemini Flash): 500 × ~400 tokens = $0.05
- Debate (Claude Sonnet): ~100 debates × 1,200 tokens = ~$0.15
- Confidence (Gemini Flash): 500 × ~300 tokens = $0.04
- **Total: ~$0.74/day**
