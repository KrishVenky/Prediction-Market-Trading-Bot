# PolySignal: Prediction Market Trading Intelligence Bot

PolySignal ingests live news signals, debates market direction with multi-agent reasoning, and outputs a confidence score plus edge for a prediction-market question.

Pipeline flow:

1. Fetch RSS headlines relevant to the question.
2. Parse and rank the most useful signals with Gemini Flash (fallback to Ollama).
3. Run a Bull vs Bear vs Arbiter debate using CrewAI.
4. Score confidence and edge for a final intelligence report.

## Features

- FastAPI backend with live SSE pipeline updates
- Single-page frontend dashboard (`frontend/index.html`)
- CLI mode for quick topic analysis
- SQLite persistence for run history, signals, and final reports
- LLM fallback routing (Gemini -> Ollama) for resilience
- Pytest suite with mocked LLM/scraper behavior

## Tech Stack

- Python 3.10+
- FastAPI + Uvicorn + SSE
- LangGraph + LangChain
- CrewAI
- Gemini 1.5 Flash and/or local Ollama (`qwen2.5:14b`)
- SQLite

## Project Structure

```text
.
|-- main.py                         # CLI entrypoint
|-- api/server.py                   # FastAPI app + SSE streaming
|-- src/pipeline/langgraph_pipeline.py
|-- src/agents/debate_crew.py
|-- src/forecasting/confidence_scorer.py
|-- scrapers/rss_scraper.py
|-- storage/db.py                   # SQLite schema + data access
|-- frontend/index.html             # Dashboard UI
|-- tests/                          # Unit tests
`-- .env.example
```

## Setup

### 1. Create and activate virtual environment

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Minimum required variable:

- `GOOGLE_API_KEY` (Gemini Flash)

Optional but recommended:

- `LANGCHAIN_TRACING_V2=true`
- `LANGCHAIN_API_KEY`
- `LANGCHAIN_PROJECT=polysignal`
- `OLLAMA_BASE_URL=http://localhost:11434`
- `OLLAMA_MODEL=qwen2.5:14b`

If you want local LLM fallback/debate via Ollama:

```bash
ollama pull qwen2.5:14b
ollama serve
```

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

Start API server:

```bash
python -m uvicorn api.server:app --reload --port 8000
```

Open:

- `http://localhost:8000`

The dashboard will show:

- live node-level pipeline progress
- parsed signals with trust scores
- final position (`YES` / `NO` / `ABSTAIN`), confidence, and edge

## Run Tests

```bash
pytest -q
```

## Data Persistence

SQLite DB file is created at:

- `polysignal.db`

It stores:

- runs metadata
- raw signals
- parsed signals
- final reports and scores

## API Summary

Detailed API docs: see `API.md`.

Main endpoints:

- `GET /` dashboard
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `POST /api/run`
- `GET /api/run/{run_id}/stream` (SSE)

## Additional Docs

- `RUNBOOK.md`: practical run and troubleshooting guide
- `API.md`: endpoint payloads and SSE event contract
- `PROJECT_GAP_ANALYSIS.md`: what is missing, what is relevant, and roadmap priorities

## Docker

Build image:

```bash
docker build -t polysignal:latest .
```

Run container:

```bash
docker run --rm -p 8000:8000 --env-file .env polysignal:latest
```

Open:

- `http://localhost:8000`

Optional persistent DB mount:

```bash
docker run --rm -p 8000:8000 --env-file .env -v "$(pwd)/polysignal.db:/app/polysignal.db" polysignal:latest
```

## Troubleshooting

- Missing Gemini key: set `GOOGLE_API_KEY` in `.env`.
- Ollama errors: ensure `ollama serve` is running and model is pulled.
- Frontend not loading: confirm server is running on port 8000 and `frontend/index.html` exists.
- Empty signal list: RSS feeds may be temporarily unavailable; retry the run.
