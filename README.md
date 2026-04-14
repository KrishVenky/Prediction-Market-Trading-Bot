# PolySignal

Prediction market intelligence system. Scrapes live news signals, runs them through a LangGraph pipeline, debates them with three CrewAI agents, and outputs a calibrated confidence score + market edge estimate.

```
RSS feeds → LangGraph → Gemini Flash (parse + score) → CrewAI debate → confidence % + edge %
```

---

## Stack

| Layer | Tech |
|---|---|
| Orchestration | LangGraph `StateGraph` |
| LLM (parse + score) | Gemini 1.5 Flash |
| LLM (debate agents) | Ollama `qwen2.5:14b` (Gemini fallback) |
| Agents | CrewAI — Bull / Bear / Arbiter |
| Scraping | feedparser (RSS), BeautifulSoup |
| Web server | FastAPI + SSE |
| Storage | SQLite |
| Tracing | LangSmith |
| Tests | pytest (51 tests, all mocked) |

---

## Setup

**1. Clone & create venv**
```bash
git clone https://github.com/KrishVenky/Prediction-Market-Trading-Bot.git
cd Prediction-Market-Trading-Bot
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac/Linux
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Configure environment**
```bash
copy .env.example .env
```
Then edit `.env`:
```env
GOOGLE_API_KEY=your_gemini_key       # aistudio.google.com/app/apikey
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_key # smith.langchain.com
LANGCHAIN_PROJECT=polysignal
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
```

**4. Pull Ollama model** (optional — falls back to Gemini if not running)
```bash
ollama pull qwen2.5:14b
ollama serve
```

---

## Usage

### Web dashboard (recommended)
```bash
.venv\Scripts\python -m uvicorn api.server:app --reload --port 8000
```
Open `http://localhost:8000` — pick a topic, click **Run Analysis**, watch the pipeline execute live.

### CLI
```bash
.venv\Scripts\python main.py
# or pass topic directly:
.venv\Scripts\python main.py "Will Bitcoin hit $100k before end of 2025?"
```

### Run tests
```bash
.venv\Scripts\python -m pytest tests/ -v
```

---

## Architecture

```
scrapers/
  rss_scraper.py        — feedparser, topic-aware feed prioritisation → RawSignal
  twitter_scraper.py    — Nitter/Playwright scraper (existing)

src/
  models.py             — RawSignal dataclass, SignalState TypedDict
  trust_score.py        — per-signal trust score (source × relevance × recency)
  llm_router.py         — Gemini Flash → Ollama fallback on rate-limit/error
  pipeline/
    langgraph_pipeline.py  — 4-node StateGraph (fetch→parse→debate→score)
  agents/
    debate_crew.py      — CrewAI Bull/Bear/Arbiter
  forecasting/
    confidence_scorer.py   — calibrated probability + market edge

api/
  server.py             — FastAPI endpoints + background pipeline runner
  sse_bus.py            — sync pipeline thread → async SSE bridge

storage/
  db.py                 — SQLite: runs, raw_signals, parsed_signals, results

frontend/
  index.html            — single-page dashboard (Tailwind, vanilla JS, SSE)

tests/                  — 51 pytest tests, zero real LLM calls
```

### Pipeline nodes

```
[FETCH]   Poll 6 RSS feeds, topic-keyword ordering, → list[RawSignal]
[PARSE]   Gemini Flash picks top 3 relevant signals, attaches trust scores
[DEBATE]  CrewAI: Bull argues YES → Bear argues NO → Arbiter decides
[SCORE]   Gemini Flash outputs confidence (0–1) + edge vs implied market price
```

### Trust score formula
```
trust = 0.40 × source_reliability
      + 0.40 × (llm_relevance / 10)
      + 0.20 × recency_score

source_reliability: metaculus=0.95, bbc=0.85, politico=0.75, coindesk=0.65
recency_score: 1.0 if <1h old, linear decay to 0.2 at 7 days
```

### LLM fallback chain
```
invoke_with_fallback()
  1. Gemini 1.5 Flash
  2. On 429 / quota: exponential backoff (2s, 4s), retry
  3. On hard error: Ollama (OLLAMA_MODEL)
  4. Both fail: raises RuntimeError with clear message
```

---

## RSS Sources

| Feed | Why |
|---|---|
| Metaculus | Live prediction questions from forecasting experts |
| BBC World / Politics | Reliable macro + political news |
| Politico | US politics, elections, legislation |
| Reuters | Global markets, economics |
| CoinDesk | Crypto / DeFi markets |

Topic keywords (`shutdown`, `tariff`, `bitcoin`, etc.) automatically reorder feeds so the most relevant ones appear first in the LLM's context window.

---

## Roadmap

- [ ] Twitter/X filtered stream integration
- [ ] Reddit scraper (`r/wallstreetbets`, `r/investing`, `r/politics`)
- [ ] Telegram signal ingestion
- [ ] ChromaDB semantic retrieval across historical signals
- [ ] Polymarket API — real market prices for edge calculation
- [ ] Automated position sizing output
