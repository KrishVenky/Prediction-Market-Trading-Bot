# PolySignal: Prediction Market Trading Intelligence Bot

PolySignal ingests live news signals, debates market direction with multi-agent reasoning, and outputs a confidence score plus edge for a prediction-market question.

Pipeline flow:

1. Fetch RSS headlines relevant to the question (22 feeds, topic-aware priority).
2. Parse and rank the most useful signals with Gemini Flash (fallback to Ollama).
3. Run a Bull vs Bear vs Arbiter debate using CrewAI.
4. Score confidence and edge for a final intelligence report.
5. Index all signals into ChromaDB for semantic search across past runs.

## Features

- FastAPI backend with live SSE pipeline updates
- Single-page frontend dashboard (`frontend/index.html`)
- CLI mode for quick topic analysis
- SQLite persistence for run history, signals, and final reports
- ChromaDB semantic search — ask any question, finds similar past signals by meaning
- Bulk feed indexer — scrapes 22 RSS feeds in parallel, indexes everything into ChromaDB
- LLM fallback routing (Gemini -> Ollama) for resilience
- Pytest suite with mocked LLM/scraper behavior (51 tests)

## Tech Stack

- Python 3.10+
- FastAPI + Uvicorn + SSE
- LangGraph + LangChain + LangSmith tracing
- CrewAI
- Gemini 1.5 Flash and/or local Ollama (`qwen2.5:14b`)
- ChromaDB (local persistent vector store)
- SQLite

## Project Structure

```text
.
|-- main.py                             # CLI entrypoint
|-- api/server.py                       # FastAPI app + SSE streaming
|-- src/pipeline/langgraph_pipeline.py  # LangGraph 4-node pipeline
|-- src/agents/debate_crew.py           # CrewAI Bull/Bear/Arbiter agents
|-- src/forecasting/confidence_scorer.py
|-- scrapers/rss_scraper.py             # feedparser RSS (22 feeds)
|-- scrapers/feed_indexer.py            # bulk feed scraper -> ChromaDB
|-- storage/db.py                       # SQLite schema + data access
|-- storage/vector_store.py             # ChromaDB semantic search
|-- frontend/index.html                 # Dashboard + search UI
|-- tests/                              # Unit tests (51)
`-- .env.example
```

## Setup

### 1. Create and activate virtual environment

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Minimum required:

- `GOOGLE_API_KEY` (Gemini Flash)

Optional but recommended:

- `LANGCHAIN_TRACING_V2=true`
- `LANGCHAIN_API_KEY`
- `LANGCHAIN_PROJECT=PolySignal`
- `OLLAMA_BASE_URL=http://localhost:11434`
- `OLLAMA_MODEL=qwen2.5:14b`

If you want local LLM fallback/debate via Ollama:

```bash
ollama pull qwen2.5:14b
ollama serve
```

### 4. Bootstrap the ChromaDB index (recommended before first run)

```bash
python -X utf8 scrapers/feed_indexer.py
```

This scrapes all 22 feeds and indexes ~300 articles into ChromaDB so semantic search works immediately.

## Run the System

### Option A: CLI run

```bash
python main.py
```

Or pass a question directly:

```bash
python main.py "Will Bitcoin exceed $100,000 before end of 2026?"
```

### Option B: Web app (recommended)

```bash
python -m uvicorn api.server:app --reload --port 8000
```

Open `http://localhost:8000`. The dashboard shows:

- Live node-by-node pipeline progress
- Parsed signals with trust scores and source badges
- Final position (`YES` / `NO` / `ABSTAIN`), confidence %, and edge %
- Semantic search panel — query any question to find similar past signals

## Semantic Search

After running one or more pipelines, use the search panel at the bottom of the dashboard to find related signals. The LLM-style search works by embedding your question and finding the most semantically similar articles in ChromaDB, ranked by cosine similarity then trust score.

You can also refresh the index manually via the **REFRESH FEEDS** button, or hit:

```bash
curl -X POST http://localhost:8000/api/index/refresh
```

## Run Tests

```bash
pytest -q
```

## Data Persistence

- `polysignal.db` — SQLite (runs, raw signals, parsed signals, results)
- `chromadb_store/` — ChromaDB vector embeddings (persisted locally)

## API Summary

Detailed API docs: see `API.md`.

Main endpoints:

- `GET /` — dashboard
- `GET /api/runs` — run history
- `GET /api/runs/{run_id}` — full run detail
- `POST /api/run` — start pipeline
- `GET /api/run/{run_id}/stream` — SSE live events
- `GET /api/search?q=<question>` — semantic signal search
- `POST /api/index/refresh` — trigger feed re-scrape

## Additional Docs

- `RUNBOOK.md` — practical run and troubleshooting guide
- `API.md` — endpoint payloads and SSE event contract
- `PROJECT_GAP_ANALYSIS.md` — what is missing, what is relevant, and roadmap priorities

## Docker

Build image:

```bash
docker build -t polysignal:latest .
```

Run container:

```bash
docker run --rm -p 8000:8000 --env-file .env polysignal:latest
```

Open `http://localhost:8000`.

Optional persistent DB mount:

```bash
docker run --rm -p 8000:8000 --env-file .env \
  -v "$(pwd)/polysignal.db:/app/polysignal.db" \
  -v "$(pwd)/chromadb_store:/app/chromadb_store" \
  polysignal:latest
```

## Troubleshooting

- **Missing Gemini key**: set `GOOGLE_API_KEY` in `.env`.
- **Ollama errors**: ensure `ollama serve` is running and model is pulled.
- **Frontend not loading**: confirm server is running on port 8000 and `frontend/index.html` exists.
- **Empty signal list**: RSS feeds may be temporarily unavailable; retry the run.
- **Search returns nothing**: run `python -X utf8 scrapers/feed_indexer.py` to bootstrap the index.
- **LangSmith traces missing**: ensure `load_dotenv()` runs before any LangChain import — already handled in all entry points.
