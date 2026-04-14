# PolySignal Runbook

This guide focuses on running, validating, and operating the system locally.

## Prerequisites

- Python 3.10+
- Internet access for RSS and Gemini API
- Optional: Ollama installed for local model fallback

## Bootstrapping

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set required key in `.env`:

- `GOOGLE_API_KEY=...`

Optional Ollama setup:

```bash
ollama pull qwen2.5:14b
ollama serve
```

## Operational Modes

## 1) CLI mode

```bash
python main.py
```

Direct topic input:

```bash
python main.py "Will there be a US government shutdown before October 2026?"
```

Expected lifecycle:

1. Node 1 fetches RSS signals.
2. Node 2 parses top signals using LLM.
3. Node 3 runs CrewAI debate.
4. Node 4 computes confidence and edge.

## 2) API + Dashboard mode

Start server:

```bash
python -m uvicorn api.server:app --reload --port 8000
```

Open UI:

- `http://localhost:8000`

Dashboard supports:

- start a new run (`POST /api/run`)
- live SSE pipeline stream (`/api/run/{run_id}/stream`)
- run history and result reloads

## 3) Docker mode

Build image:

```bash
docker build -t polysignal:latest .
```

Run:

```bash
docker run --rm -p 8000:8000 --env-file .env polysignal:latest
```

Open UI:

- `http://localhost:8000`

Persist SQLite locally:

```bash
docker run --rm -p 8000:8000 --env-file .env -v "$(pwd)/polysignal.db:/app/polysignal.db" polysignal:latest
```

## Validation Checklist

Use this quick checklist after startup:

1. Server starts without import errors.
2. Visiting `/` returns dashboard HTML.
3. Starting a run returns a `run_id`.
4. SSE stream receives `node_start`, `node_done`, and `complete` events.
5. `/api/runs/{run_id}` returns run, parsed signals, and result.
6. `polysignal.db` is created and has data.

## Test Commands

Run all tests:

```bash
pytest -q
```

Run selected tests:

```bash
pytest tests/test_pipeline_nodes.py -q
pytest tests/test_scorer.py -q
pytest tests/test_rss_scraper.py -q
```

## Common Failures

## Missing GOOGLE_API_KEY

Symptom:

- CLI warns about missing env var
- parse/score nodes fail and use fallbacks

Fix:

- set `GOOGLE_API_KEY` in `.env`

## Ollama unavailable

Symptom:

- debate router logs Ollama not reachable

Fix:

```bash
ollama serve
ollama pull qwen2.5:14b
```

## RSS source instability

Symptom:

- low or zero fetched signals

Fix:

- retry later (source-side issue)
- reduce dependence on a single feed

## Port already in use

Symptom:

- Uvicorn fails on port 8000

Fix:

```bash
python -m uvicorn api.server:app --reload --port 8001
```

Then open `http://localhost:8001`.

## What matters next

For a prioritized missing-features and relevance assessment, see:

- `PROJECT_GAP_ANALYSIS.md`
