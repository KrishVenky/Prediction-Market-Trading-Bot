# CLAUDE.md — PolySignal

Complete context for AI assistants working on this codebase.

---

## DEMO QUICK-START (read this first)

> **If you just did `git pull` and need to run this for a demo — do exactly this:**

```bat
# Windows — double-click or run in terminal:
start.bat
```

That script handles everything: creates the venv, installs deps, runs tests, starts the server. Open **http://localhost:8000** when it says `Launching`.

**If you're setting up on a new machine from scratch:**

```bash
git clone https://github.com/KrishVenky/Prediction-Market-Trading-Bot.git
cd Prediction-Market-Trading-Bot
copy .env.example .env        # then edit .env with your keys
start.bat
```

**Minimum .env you need for the demo to work:**

```ini
GROQ_API_KEY=gsk_...          # get free at console.groq.com — most reliable
GOOGLE_API_KEY=...            # from aistudio.google.com — optional, quota exhausts fast
```

Groq is the primary LLM at demo time — Gemini free-tier quota exhausts after ~5 runs. If you only have one key, make it Groq.

**What to demo:**
1. Open http://localhost:8000
2. Type a question like `Will the Fed cut rates before July 2025?`
3. Hit Analyse — watch all 4 nodes fire live (takes ~30–60s)
4. Point at: the TOML inter-agent messages tab, the Data Pipeline Lineage panel, the Bull/Bear/Arbiter debate, and the final confidence score + edge

---

## What This Is

**PolySignal** is a prediction market intelligence pipeline. Given a market question (e.g. "Will the Fed cut rates before July 2025?"), it:
1. Scrapes 22 RSS feeds for relevant signals
2. Uses an LLM to parse and score the top 3 signals
3. Runs a 3-agent CrewAI debate (Bull / Bear / Arbiter)
4. Produces a confidence score, market edge, and position (YES/NO/ABSTAIN)

It exposes a FastAPI server with SSE streaming and a single-file HTML frontend. The whole pipeline is visible live in the browser as it runs.

---

## Project Layout

```
Prediction-Market-Trading-Bot/
├── api/
│   ├── server.py          # FastAPI app — all HTTP endpoints + SSE
│   └── sse_bus.py         # asyncio Queue bridge (thread → async)
├── scrapers/
│   ├── rss_scraper.py     # feedparser, 22 feeds, browser UA header
│   ├── feed_indexer.py    # bulk RSS→ChromaDB indexer (parallel, 6 workers)
│   ├── twitter_importer.py # JSON→ChromaDB (flat/nested/profile formats)
│   └── twitter_scraper.py  # Playwright scraper (Nitter dead — not active)
├── src/
│   ├── llm_router.py      # Gemini→Groq→Ollama fallback chain
│   ├── message_format.py  # TOML inter-agent message builder/parser
│   ├── models.py          # SignalState TypedDict + RawSignal dataclass
│   ├── trust_score.py     # Trust score formula (source + LLM + recency)
│   ├── agents/
│   │   └── debate_crew.py # CrewAI Bull/Bear/Arbiter agents
│   ├── forecasting/
│   │   └── confidence_scorer.py # Final confidence + edge calculation
│   └── pipeline/
│       └── langgraph_pipeline.py # LangGraph StateGraph (4 nodes)
├── storage/
│   ├── db.py              # SQLite layer (raw sqlite3, threading.Lock)
│   └── vector_store.py    # ChromaDB semantic search + bulk indexing
├── frontend/
│   └── index.html         # Single-file dashboard (vanilla JS + SSE)
├── tests/                 # pytest suite (51 tests, all must pass)
├── main.py                # CLI entry point (no server)
├── .env                   # API keys (not committed)
├── .env.example           # Template
├── requirements.txt
├── Dockerfile
├── polysignal.db          # SQLite DB (gitignored, auto-created)
└── chromadb_store/        # ChromaDB persistence (gitignored, auto-created)
```

---

## Running the Project

```bash
# Start the web server
python -m uvicorn api.server:app --reload --port 8000
# Open http://localhost:8000

# CLI mode (no server, prints report to terminal)
python main.py

# Run all tests (must be 51/51)
.venv/Scripts/python.exe -X utf8 -m pytest tests/ -q

# Bulk index all RSS feeds into ChromaDB (~300 articles, ~14s)
python scrapers/feed_indexer.py

# Import Twitter JSON into ChromaDB
python scrapers/twitter_importer.py path/to/tweets.json
python scrapers/twitter_importer.py path/to/tweets.json --dry-run
```

---

## Environment Variables (.env)

```ini
# Google Gemini — https://aistudio.google.com
GOOGLE_API_KEY=...

# Groq (free tier, massive RPM) — https://console.groq.com
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile

# Ollama (local, optional) — https://ollama.ai
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b

# LangSmith tracing (optional)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=polysignal
```

**Critical**: Every file that uses these keys must load the .env with an **explicit path**, not `load_dotenv()` alone (CWD-dependent and breaks in production):

```python
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
# Adjust the number of .parent calls to reach the repo root
```

Files that already do this correctly: `main.py`, `api/server.py`, `src/llm_router.py`, `src/pipeline/langgraph_pipeline.py`, `src/agents/debate_crew.py`.

---

## LLM Stack

### invoke_with_fallback (src/llm_router.py)
Used by nodes 2 (parse) and 4 (score). Chain:

1. **Gemini** — tries `gemini-2.0-flash` → `gemini-2.0-flash-lite` → `gemini-2.5-flash`
   - Skip immediately on 404 (model deprecated/not-found)
   - Exponential backoff on 429 (4s, 16s), then move to next model
   - **`gemini-1.5-flash` is permanently 404 — never use it**
   - Free-tier daily quota exhausts quickly; `gemini-2.5-flash` is the most reliable

2. **Groq** — `llama-3.3-70b-versatile` via `langchain-groq`
   - Free tier, very high RPM, sub-second latency
   - **Confirmed working** — primary practical fallback

3. **Ollama** — `qwen2.5:14b` local
   - Zero cost but requires `ollama serve && ollama pull qwen2.5:14b`
   - Last resort; not available in most deployments

### _pick_llm (src/agents/debate_crew.py)
Used by node 3 (CrewAI debate). Same priority order, but uses `crewai.LLM` + LiteLLM:

1. Ollama (reachability check via `/api/tags`)
2. **Groq** (`groq/llama-3.3-70b-versatile`) — primary fallback
3. Gemini (`gemini/gemini-2.5-flash` first)

Requires `litellm` package for Groq routing in CrewAI. Already installed.

### Adding a new LLM provider
- For `invoke_with_fallback`: add a `_provider()` builder function and insert it in the try-chain in `invoke_with_fallback()`
- For CrewAI: add an `elif` block in `_pick_llm()` returning `LLM(model="provider/model", api_key=key)`
- LiteLLM prefix mapping: `groq/`, `anthropic/`, `openrouter/`, `cohere/`, `mistral/`

---

## Pipeline (LangGraph)

```
fetch → parse → debate → score → END
```

**State** (`src/models.py` — `SignalState` TypedDict):
```python
{
    "topic":            str,          # market question
    "raw_signals":      list[RawSignal],
    "parsed_signals":   list[dict],
    "debate_result":    dict,         # bull/bear/verdict/position
    "confidence_score": float,
    "edge":             float,
    "final_output":     str,
}
```

**Node contract**: each node receives the full state dict, returns only the keys it sets.

**SSE events**: every node calls `_emit(event_type, **payload)`. The server wires `set_event_callback(cb, run_id=run_id)` before calling `run_pipeline()`. Nodes emit:
- `node_start` — when beginning
- `node_done` — when complete, includes `toml=` field with full TOML message

**TOML messages**: every node builds a typed TOML document via `src/message_format.py` and:
1. Emits it over SSE in the `toml` field
2. Persists it to `agent_messages` table via `_store_agent_message()`

---

## Database (SQLite — storage/db.py)

File: `polysignal.db` at repo root. Schema:

| Table | Purpose |
|-------|---------|
| `runs` | One row per pipeline run (id, topic, status, created_at) |
| `raw_signals` | All scraped headlines for a run |
| `parsed_signals` | LLM-selected signals with sentiment/trust/relevance |
| `results` | Final position, confidence, edge, bull/bear/verdict |
| `agent_messages` | TOML inter-agent messages per node |
| `source_index` | Feed registry with trust scores and article counts |

Key functions:
- `init_db()` — idempotent, `CREATE TABLE IF NOT EXISTS`, safe to call on startup
- `insert_agent_message(run_id, node, msg_type, toml_str)` — called by each pipeline node
- `get_pipeline_stats()` — aggregate stats for the frontend lineage panel (reads from `parsed_signals`, not `raw_signals` — the latter lacks `trust_score`)
- `upsert_source(name, type, url, trust)` / `increment_source_count()` — used by feed_indexer and twitter_importer

**Threading**: all writes go through `threading.Lock()`. The pipeline runs in a `ThreadPoolExecutor(max_workers=2)`, so this matters.

---

## Vector Store (ChromaDB — storage/vector_store.py)

Persistent store at `chromadb_store/`. Collection: `polysignal_signals`. Cosine similarity.

Two indexing paths:
- `index_signals(run_id, parsed, run_topic)` — called after each pipeline run
- `index_raw_articles(articles)` — bulk upsert from feed_indexer/twitter_importer

Search:
```python
results = semantic_search("Will the Fed cut rates?", n_results=8, min_trust=0.6)
```
- `min_similarity=0.20` noise floor — anything below is unrelated (old default returned Greek migrants for Fed queries)
- Over-fetches 4× then filters, deduplicates titles
- Sorted by similarity DESC, then trust_score DESC

---

## TOML Inter-Agent Messages (src/message_format.py)

Each node produces a typed TOML document. Format:

```toml
[message]
type      = "score_complete"
run_id    = "abc-123"
timestamp = "2026-04-14T10:00:00Z"
node      = "score"
llm_model = "groq/llama-3.3-70b"

[market]
question = "Will the Fed cut rates before July 2025?"
position = "NO"

[forecast]
confidence   = 0.72
market_price = 0.55
edge         = 0.17
reasoning    = "..."

[interpretation]
edge_label = "strong_buy"
signal     = "NO — STRONG BUY"
```

`_edge_label()` thresholds: `≥0.15` → `strong_buy`, `≥0.05` → `buy`, `≥-0.05` → `hold`, `≥-0.15` → `sell`, else `strong_sell`.

Python 3.10 compatibility: `tomllib` is stdlib in 3.11+; on 3.10 it falls back to `tomli` package (already installed in .venv).

---

## RSS Feeds (scrapers/rss_scraper.py)

22 active feeds across 5 categories. Max 4 items per feed per pipeline run (88 signals total).

| Category | Feeds |
|----------|-------|
| Politics | `politico`, `bbc_politics`, `the_hill`, `axios`, `guardian_us` |
| World | `bbc_world`, `guardian_world` |
| Economy | `wsj_markets`, `wsj_economy`, `marketwatch`, `cnbc_economy`, `cnbc_markets`, `ft`, `guardian_biz`, `npr_economy`, `forexlive`, `seeking_alpha`, `zerohedge` |
| Crypto | `cointelegraph`, `decrypt`, `theblock` |
| Tech | `techcrunch` |

**Dead feeds** (return 0 articles — blocked or moved): Reuters, AP News, CoinDesk, Metaculus. Do not re-add without testing.

`fetch_feed()` uses a browser User-Agent header. Without it, many feeds return 403/empty.

`_TOPIC_FEEDS` maps topic keywords → subset of feeds to prioritise. Edit this to tune which feeds are polled for which topics.

---

## Source Trust Scores

Static trust lookup in `src/message_format.py` (`_SOURCE_TRUST`) and `scrapers/twitter_importer.py` (`_ACCOUNT_TRUST`). Dynamic formula in `src/trust_score.py` combines:
- Source baseline reliability
- LLM-assigned relevance score
- Recency (hours since published)

Trust score ranges:
- `0.90+` — Fed/Metaculus/primary-source official accounts
- `0.80–0.89` — Reuters, AP, FT, BBC, Guardian
- `0.70–0.79` — Politico, Axios, CNBC, NPR
- `0.60–0.69` — ZeroHedge, CoinTelegraph, Twitter accounts
- `<0.60` — Unknown sources (default 0.55)

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serves `frontend/index.html` |
| GET | `/api/runs` | Last 30 runs |
| GET | `/api/runs/{run_id}` | Run + signals + result |
| POST | `/api/run` | Start pipeline `{"topic": "..."}` |
| GET | `/api/run/{run_id}/stream` | SSE stream of live events |
| GET | `/api/runs/{run_id}/messages` | TOML inter-agent messages |
| GET | `/api/search?q=...&n=8&min_trust=0.0` | Semantic search |
| GET | `/api/search/stats` | ChromaDB size |
| GET | `/api/stats` | Aggregate pipeline stats |
| POST | `/api/index/refresh` | Background feed re-index |

**SSE event types**: `node_start`, `node_done`, `complete`, `error`, `heartbeat`, `done`

`node_done` payload always includes a `toml` field with the full inter-agent TOML string.

---

## Frontend (frontend/index.html)

Single-file, no build step, vanilla JS. Key panels:

- **Run controls** — topic input, submit button, active model indicator (green header)
- **Live pipeline** — SSE-driven node progress, confidence meter, position badge
- **Top signals** — source, sentiment, trust score
- **Bull / Bear / Arbiter** — debate transcript
- **Data Pipeline Lineage** — total_raw / total_parsed / runs + per-source bar chart with trust %
- **Inter-Agent TOML Messages** — FETCH / PARSE / DEBATE / SCORE tabs, monospace green viewer

Frontend functions to know:
- `startRun()` — POSTs to `/api/run`, opens SSE stream
- `updateTomlViewer(node, tomlStr)` — called on each `node_done` SSE event
- `loadSourceStats()` — fetches `/api/stats` and renders the lineage panel
- `initTomlTabs()` — wires up the tab click handlers on page load

---

## Tests

```bash
.venv/Scripts/python.exe -X utf8 -m pytest tests/ -q
# Expected: 51 passed
```

Test files:
- `tests/test_models.py` — SignalState, RawSignal, empty_state
- `tests/test_pipeline_nodes.py` — node functions (mocked LLMs)
- `tests/test_rss_scraper.py` — feed parsing, topic filtering
- `tests/test_scorer.py` — trust score formula, edge calculation

`tests/conftest.py` mocks `invoke_with_fallback` and `run_debate` so tests never hit real APIs.

**Always run tests before committing.** The `-X utf8` flag is required on Windows to avoid cp1252 encoding errors in print output.

---

## Known Issues & Constraints

| Issue | Status | Fix |
|-------|--------|-----|
| Gemini free-tier daily quota exhausts after ~5 pipeline runs | Active | Groq fallback handles it automatically |
| `gemini-1.5-flash` returns 404 | Permanent | Removed from all model lists |
| Nitter (Twitter scraping) all instances dead | Active | Use `twitter_importer.py` with manually-scraped JSON instead |
| `tomllib` missing on Python 3.10 | Fixed | `try: import tomllib except: import tomli` |
| Windows cp1252 encoding errors in print | Fixed | Use `═` only in print statements, not in dividers piped to files. Run with `-X utf8`. |
| ChromaDB similarity noise (unrelated results) | Fixed | `min_similarity=0.20` floor in `semantic_search()` |

---

## Future Implementation Guide

### Adding a new pipeline node

1. Write `node_xyz(state: SignalState) -> dict` in `langgraph_pipeline.py`
2. Emit `_emit("node_start", node="xyz", message="...")`
3. Build a TOML message with a new `build_xyz_message()` in `message_format.py`
4. Emit `_emit("node_done", node="xyz", toml=toml_msg)` and call `_store_agent_message()`
5. Add `builder.add_node("xyz", node_xyz)` and wire edges in `build_pipeline()`
6. Add a tab in the frontend TOML viewer for the new node

### Adding a new data source

1. Write a scraper that returns a list of dicts with at minimum: `id`, `url`, `title`, `text`, `source`, `trust_score`
2. Call `index_raw_articles(articles)` to push into ChromaDB
3. Call `upsert_source()` + `increment_source_count()` to register in `source_index`
4. Add a trust score entry in `_SOURCE_TRUST` (message_format.py) and/or `_ACCOUNT_TRUST` (twitter_importer.py)
5. Update `_TOPIC_FEEDS` in rss_scraper.py if it's an RSS feed

### Adding Polymarket/Kalshi live price data

- Polymarket REST API: `https://gamma-api.polymarket.com/markets`
- Kalshi REST API: authenticated, needs account + API key
- Store fetched prices in the `results` table as `market_price`
- Currently `market_price` is LLM-estimated — replacing it with real data would make edge calculations accurate
- Suggested new node: `node_price` between `fetch` and `parse`, attaches `{"market_price": float}` to state

### Adding persistent memory / feedback loop

- Store final position + actual market outcome (resolved YES/NO) in a new `outcomes` table
- After resolution, compute calibration error and feed back into trust score adjustments
- `confidence_scorer.py` already has placeholder logic for this — extend `score_confidence()`

### Improving semantic search quality

- Current encoder: ChromaDB default (all-MiniLM-L6-v2 via sentence-transformers)
- For financial domain: consider `BAAI/bge-base-en-v1.5` or OpenAI `text-embedding-3-small`
- To swap: change `_client.get_or_create_collection()` call to pass an `embedding_function`
- Need to re-index all existing documents after changing the embedding model

### Containerization

`Dockerfile` is already present. To build:
```bash
docker build -t polysignal .
docker run -p 8000:8000 --env-file .env polysignal
```
ChromaDB and SQLite paths are relative to the repo root — mount a volume for persistence in production.

### Connecting the Telegram bot

A separate Telegram bot for Indian markets (NSE/BSE) exists in the parent directory. Integration path:
- Bot identifies a market question → calls `POST /api/run` with the topic
- Polls `GET /api/runs/{run_id}` or connects to SSE stream
- On `complete` event, formats and sends the position + reasoning back to the Telegram channel

---

## Commit Conventions

```
feat: short description of new capability
fix: what was broken and what the fix is
refactor: what changed and why (no behavior change)
test: test additions/changes
```

Always run tests before committing. Never commit `.env` or `polysignal.db` or `chromadb_store/`.
