# CLAUDE.md — PolySignal Full Build Spec

This file is the authoritative spec for building the full production system.
Do not start implementation until the MVP (class demo) is shipped.

---

## Project Context

**Goal:** Geographic information-arbitrage on prediction markets (Polymarket, Bitget).  
**Edge:** Monitor Indian/global primary sources during US sleep hours (2–6 AM EST = 12:30–4:30 PM IST).  
**Capital:** ₹20,000 ($240) initial deployment.  
**Timeline:** 6 months, 4 phases.

---

## Repo Structure (Target)

```
PolymarketTradingBot/
├── src/
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── main.py                  # entry point — starts all pollers + graph
│   │   ├── rss_scraper.py           # feedparser + async polling loop
│   │   ├── reddit_scraper.py        # PRAW async wrapper
│   │   ├── twitter_poller.py        # Apify Twitter scraper (Phase 2)
│   │   ├── discord_monitor.py       # discord.py bot (Phase 2)
│   │   ├── vision_processor.py      # GPT-4o image → text description
│   │   └── langgraph_pipeline.py    # full StateGraph definition
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── debate_crew.py           # CrewAI Crew factory
│   │   ├── bull_agent.py
│   │   ├── bear_agent.py
│   │   ├── base_rate_agent.py
│   │   ├── market_agent.py
│   │   └── arbiter_agent.py
│   ├── forecasting/
│   │   ├── __init__.py
│   │   ├── confidence_scorer.py
│   │   ├── edge_calculator.py
│   │   └── kelly_sizing.py
│   ├── risk/
│   │   ├── __init__.py
│   │   └── portfolio_manager.py
│   ├── integrations/
│   │   ├── polymarket_client.py     # CLOB API wrapper
│   │   ├── bitget_client.py         # Bitget futures API
│   │   └── telegram_alert.py        # bot + manual approval flow
│   └── utils/
│       ├── toml_schema.py           # TOML ↔ dict helpers
│       ├── signal_dedup.py          # Redis-backed dedup
│       ├── base_rates.py            # historical frequency lookup
│       └── logging_config.py
├── config/
│   ├── sources.toml                 # RSS/Reddit/Twitter source definitions
│   ├── risk.toml                    # Kelly params + portfolio limits
│   └── markets.toml                 # Active Polymarket market IDs + metadata
├── data/
│   ├── polymarket.db                # SQLite: signals, verdicts, outcomes
│   └── base_rates.toml              # historical event frequencies (manual seed)
├── tests/
│   ├── test_pipeline.py
│   ├── test_agents.py
│   └── test_risk.py
├── .env.example
├── requirements.txt
├── README.md                        # class-facing doc
├── system.md                        # architecture reference
└── CLAUDE.md                        # this file
```

---

## Environment Variables (Full)

```bash
# LLM APIs
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=

# Social Media
TWITTER_BEARER_TOKEN=        # or Apify API key for Twitter scraping
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=polysignal/0.1

# Discord
DISCORD_BOT_TOKEN=

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Prediction Markets
POLYMARKET_API_KEY=
POLYMARKET_PRIVATE_KEY=      # for CLOB execution
BITGET_API_KEY=
BITGET_SECRET_KEY=
BITGET_PASSPHRASE=

# Infrastructure
DATABASE_URL=sqlite:///data/polymarket.db
REDIS_URL=redis://localhost:6379/0

# Operational
LOG_LEVEL=INFO
DRY_RUN=true                 # set false only when real-money trading
MIN_CONFIDENCE=80
MIN_EDGE=0.20
MAX_BANKROLL_PCT=0.40
```

---

## Phase Roadmap

### Phase 1 — MVP / Class Demo (current)
- [x] README, system.md, CLAUDE.md
- [ ] `rss_scraper.py` — feedparser, async polling, RawSignal dataclass
- [ ] `reddit_scraper.py` — PRAW, async, flair filtering
- [ ] `langgraph_pipeline.py` — full StateGraph, all nodes stubbed
- [ ] `debate_crew.py` — CrewAI 5-agent setup, TOML message bus
- [ ] `confidence_scorer.py` + `edge_calculator.py` + `kelly_sizing.py`
- [ ] SQLite schema + migrations
- [ ] `base_rates.toml` — seed with 20 common event types
- [ ] End-to-end demo: RSS signal → LangGraph → CrewAI → console output

### Phase 2 — Data Sources (Weeks 5-8)
- [ ] `twitter_poller.py` — Apify actor for Tier 0/1 accounts
- [ ] `discord_monitor.py` — discord.py, monitor Project Owl + Faytuks
- [ ] `vision_processor.py` — GPT-4o vision, handle image/video URLs
- [ ] Redis queue — decouple ingestion from processing
- [ ] Automated dedup — sha256 signal IDs, Redis TTL

### Phase 3 — Intelligence Upgrade (Weeks 9-12)
- [ ] Historical base rate DB — backfill from resolved Polymarket markets
- [ ] `base_rate_agent.py` — query historical DB, return frequency
- [ ] Calibration tracking — Brier score per agent, auto-reweight
- [ ] Pattern recognition — detect repeat event types, apply learned priors
- [ ] `market_agent.py` — live Polymarket CLOB order book parsing

### Phase 4 — Execution + Risk (Weeks 13-16)
- [ ] `polymarket_client.py` — CLOB API: get prices, submit limit orders
- [ ] `bitget_client.py` — futures API for crypto markets
- [ ] `telegram_alert.py` — formatted alerts, inline keyboard for manual approve/reject
- [ ] `portfolio_manager.py` — enforce all hard limits, track open positions
- [ ] DRY_RUN mode — full pipeline execution without actual order submission
- [ ] Real-money deployment — ₹20,000 initial, track against paper baseline

### Phase 5 — Scale (Months 5-6)
- [ ] Kalshi integration (US markets)
- [ ] ML signal filter — train on historical verdicts to reduce false positives
- [ ] Dashboard — Streamlit or Next.js, live P&L + calibration charts
- [ ] Increase capital to ₹50K–1L based on Phase 4 performance

---

## Implementation Notes

### rss_scraper.py

Use `feedparser` + `asyncio` for concurrent polling. Key considerations:
- Store `last_seen_id` per feed in Redis to avoid reprocessing
- Parse `entry.published_parsed` → UTC datetime
- Extract all `media_content` URLs for vision processing
- Tier assignment comes from `config/sources.toml`

```python
import feedparser
import asyncio
from datetime import datetime, timezone

async def poll_feed(feed_cfg: dict, redis_client, signal_queue: asyncio.Queue):
    while True:
        parsed = feedparser.parse(feed_cfg["url"])
        last_seen = await redis_client.get(f"last:{feed_cfg['url']}")
        
        new_entries = [e for e in parsed.entries if e.id != last_seen]
        for entry in new_entries:
            signal = RawSignal(
                id=sha256_id(feed_cfg["url"], entry.id),
                source=feed_cfg["name"],
                tier=feed_cfg["tier"],
                content=entry.get("summary", entry.get("title", "")),
                media_urls=extract_media(entry),
                timestamp=datetime(*entry.published_parsed[:6], tzinfo=timezone.utc),
                category=feed_cfg["category"],
            )
            await signal_queue.put(signal)
        
        if new_entries:
            await redis_client.set(f"last:{feed_cfg['url']}", new_entries[0].id)
        
        await asyncio.sleep(feed_cfg["poll_sec"])
```

### langgraph_pipeline.py

Full graph definition. Import all nodes, wire edges, compile. The graph is compiled once at startup and invoked per-signal batch.

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(SignalState)
graph.add_node("ingest",             ingest_node)
graph.add_node("vision_processor",   vision_processor_node)
graph.add_node("normalizer",         normalizer_node)
graph.add_node("llm_parser",         llm_parser_node)
graph.add_node("correlator",         correlator_node)
graph.add_node("crewai_debate",      crewai_debate_node)
graph.add_node("confidence_scorer",  confidence_scorer_node)
graph.add_node("edge_calculator",    edge_calculator_node)
graph.add_node("alert_node",         alert_node)

graph.set_entry_point("ingest")
graph.add_edge("ingest",            "vision_processor")
graph.add_edge("vision_processor",  "normalizer")
graph.add_edge("normalizer",        "llm_parser")
graph.add_edge("llm_parser",        "correlator")
graph.add_conditional_edges("correlator", should_debate, {
    "crewai_debate":     "crewai_debate",
    "confidence_scorer": "confidence_scorer",
})
graph.add_edge("crewai_debate",     "confidence_scorer")
graph.add_edge("confidence_scorer", "edge_calculator")
graph.add_edge("edge_calculator",   "alert_node")
graph.add_edge("alert_node",        END)

pipeline = graph.compile()
```

### toml_schema.py

```python
import tomllib
import tomli_w   # pip install tomli-w (stdlib tomllib is read-only)

def signal_to_toml(signal: RawSignal) -> str:
    d = {
        "signal": {
            "source": signal.source,
            "tier": signal.tier,
            "timestamp": signal.timestamp.isoformat(),
        }
    }
    return tomli_w.dumps(d)

def toml_to_dict(toml_str: str) -> dict:
    return tomllib.loads(toml_str)
```

### Polymarket CLOB API

Docs: https://docs.polymarket.com  
Authentication: EIP-712 signatures with private key  
Key endpoints:
- `GET /markets` — list active markets
- `GET /book?token_id=...` — order book
- `POST /order` — submit limit order

Use `py-clob-client` library (official Polymarket SDK).

### Telegram Alert Format

```
🚨 ARBITRAGE SIGNAL

Market: RBI Rate Cut – April 2026
Confidence: 91/100
Edge: +49% (our: 91% · market: 42%)
Position: ₹800 (5% bankroll)
EV: +₹392

Sources: RBI RSS (Tier 0) + LiveMint + r/IndiaInvestments
Vision: ✓ Chart confirmed 25bps cut

[✅ APPROVE] [❌ REJECT] [🔍 DETAILS]
```

Manual approval via inline keyboard before any order submission.

---

## Testing Strategy

### Unit Tests
- `test_toml_schema.py` — round-trip serialization
- `test_kelly_sizing.py` — known inputs, verify Kelly formula
- `test_portfolio_manager.py` — test all hard limit violations

### Integration Tests
- `test_pipeline.py` — feed a mock RawSignal through full LangGraph graph, assert alert_ready=True for high-confidence signal
- `test_agents.py` — run CrewAI debate with mock TOML event, assert arbiter returns float in [0,1]

### End-to-End (manual)
- Point RSS scraper at live RBI feed, verify signal flows through to console alert
- Verify DRY_RUN blocks actual order submission

---

## Known Risks + Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| Twitter API cost spike | High | Apify batch scraping, cache aggressively |
| LLM hallucinated event | Medium | Require Tier 0 source OR ≥3 agreeing sources |
| Reddit rate limit | Low | PRAW OAuth, 30 req/min budget, backoff |
| Model cost overrun | Medium | Gemini Flash for all non-vision tasks, GPT-4o only for images |
| Market already priced in | Medium | Check edge recalculate at execution time (staleness check) |
| False positive trade | Low–Medium | Manual Telegram approval gate before any real order |
| Capital loss | Medium | Quarter-Kelly, 5% max position, 40% max deployment |

---

## Notes for Future Me

- The TOML inter-agent format was deliberately chosen over JSON for token efficiency — keep it, don't "simplify" to JSON.
- CrewAI's `Process.sequential` is correct for this crew — Arbiter MUST see the other four verdicts before synthesizing.
- `skip_debate` short-circuit is load-bearing — low-signal noise events shouldn't spin up 5 LLM calls.
- `base_rates.toml` needs manual seeding from resolved Polymarket history — this is the most important cold-start step.
- The confidence formula weights (0.40 / 0.25 / 0.20 / 0.15) were chosen based on information quality, not tuned empirically. Revisit after 50+ resolved signals.
- DRY_RUN=true should be the default until Phase 4 is validated end-to-end.
- Kalshi integration (Phase 5) needs US residency for API access — check legal status before building.
