"""
PolySignal  —  FastAPI Web Server
-----------------------------------
Endpoints:
  GET  /                       → dashboard (frontend/index.html)
  GET  /api/runs               → list recent pipeline runs
  GET  /api/runs/{run_id}      → single run + signals + result
  POST /api/run                → start a new pipeline run
  GET  /api/run/{run_id}/stream → SSE stream of live node events

Run with:
  python -m uvicorn api.server:app --reload --port 8000
"""

from __future__ import annotations

# ── Load .env FIRST — before any LangChain/LangGraph import ──────────────────
import os
from pathlib import Path
from dotenv import load_dotenv
# Use explicit path so it works regardless of working directory
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

import asyncio
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import storage.db as db
from storage.vector_store import collection_size, index_signals, semantic_search
from api.sse_bus import SENTINEL, close_sync, emit_sync, register_loop, subscribe, unsubscribe

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="PolySignal API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=2)
_FRONTEND = Path(__file__).parent.parent / "frontend" / "index.html"


@app.on_event("startup")
async def startup():
    db.init_db()
    register_loop(asyncio.get_event_loop())


# ── Static frontend ────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    if not _FRONTEND.exists():
        return JSONResponse({"error": "frontend/index.html not found"}, status_code=404)
    return FileResponse(_FRONTEND, media_type="text/html")


# ── Runs list ──────────────────────────────────────────────────────────────────

@app.get("/api/runs")
async def list_runs():
    return db.get_runs(limit=30)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "run":            run,
        "raw_signals":    db.get_raw_signals(run_id),
        "parsed_signals": db.get_parsed_signals(run_id),
        "result":         db.get_result(run_id),
    }


# ── Semantic search ────────────────────────────────────────────────────────────

@app.get("/api/search")
async def search_signals(q: str = "", n: int = 8, min_trust: float = 0.0):
    """
    Semantic search over all indexed signals.
    Returns signals ranked by cosine similarity to `q`, then by trust score.
    """
    if not q.strip():
        raise HTTPException(400, "q (query) is required")
    results = semantic_search(q.strip(), n_results=n, min_trust=min_trust)
    return {
        "query":   q,
        "count":   len(results),
        "indexed": collection_size(),
        "results": results,
    }


@app.get("/api/search/stats")
async def search_stats():
    return {"indexed_signals": collection_size()}


@app.post("/api/index/refresh")
async def refresh_index():
    """
    Trigger a full RSS + Twitter feed scrape and index into ChromaDB.
    Runs in a background thread so the request returns immediately.
    """
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_feed_index_bg)
    return {"status": "indexing", "message": "Feed index refresh started in background"}


def _run_feed_index_bg() -> None:
    """Background task: scrape all feeds and index into ChromaDB."""
    try:
        from scrapers.feed_indexer import run_full_index
        run_full_index()
    except Exception as exc:
        print(f"  [INDEX] Feed indexer error: {exc}")


# ── Start pipeline ─────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    topic: str


@app.post("/api/run")
async def start_run(body: RunRequest):
    topic  = body.topic.strip()
    if not topic:
        raise HTTPException(400, "topic is required")

    run_id = str(uuid.uuid4())
    now    = _utcnow()
    db.create_run(run_id, topic, now)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_pipeline_bg, run_id, topic)

    return {"run_id": run_id, "topic": topic, "started_at": now}


# ── SSE stream ─────────────────────────────────────────────────────────────────

@app.get("/api/run/{run_id}/stream")
async def stream_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    q = subscribe(run_id)

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue

                if event.get("type") == SENTINEL:
                    yield {"event": "done", "data": "{}"}
                    break

                yield {"event": event.get("type", "update"), "data": json.dumps(event)}
        finally:
            unsubscribe(run_id)

    return EventSourceResponse(generator())


# ── Background pipeline runner ─────────────────────────────────────────────────

def _run_pipeline_bg(run_id: str, topic: str) -> None:
    """Runs in a ThreadPoolExecutor. Emits SSE events and writes to DB."""
    from src.pipeline.langgraph_pipeline import run_pipeline, set_event_callback

    def on_event(event_type: str, payload: dict) -> None:
        emit_sync(run_id, {"type": event_type, **payload})

    set_event_callback(on_event)

    try:
        final_state = run_pipeline(topic)

        # Persist results
        now = _utcnow()
        parsed = final_state.get("parsed_signals", [])
        db.insert_raw_signals(run_id, final_state.get("raw_signals", []), now)
        db.insert_parsed_signals(run_id, parsed)
        db.insert_result(run_id, final_state, now)
        db.finish_run(run_id, "complete", now)

        # Index parsed signals into ChromaDB for semantic search
        index_signals(run_id, parsed, run_topic=topic)

        emit_sync(run_id, {
            "type":       "complete",
            "confidence": final_state.get("confidence_score", 0),
            "edge":       final_state.get("edge", 0),
            "position":   final_state.get("debate_result", {}).get("position", "?"),
        })

    except Exception as exc:
        db.finish_run(run_id, "error", _utcnow())
        emit_sync(run_id, {"type": "error", "message": str(exc)})

    finally:
        set_event_callback(None)
        close_sync(run_id)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Dev entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
