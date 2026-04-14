# PolySignal API Reference

Base URL (local):

- `http://localhost:8000`

## Endpoints

## GET /

Returns dashboard page (`frontend/index.html`).

Response:

- `200 text/html`
- `404` JSON if frontend file is missing

## GET /api/runs

Returns most recent runs (up to 30).

Response example:

```json
[
  {
    "id": "uuid",
    "topic": "Will Bitcoin hit 100k?",
    "status": "complete",
    "created_at": "2026-04-14T12:00:00+00:00",
    "finished_at": "2026-04-14T12:00:19+00:00"
  }
]
```

## GET /api/runs/{run_id}

Returns a complete run payload.

Response shape:

```json
{
  "run": { "id": "...", "topic": "...", "status": "..." },
  "raw_signals": [
    { "source": "bbc_world", "title": "...", "url": "..." }
  ],
  "parsed_signals": [
    {
      "source": "reuters",
      "title": "...",
      "signal_text": "...",
      "sentiment": "bullish",
      "trust_score": 0.74
    }
  ],
  "result": {
    "position": "YES",
    "confidence": 0.67,
    "edge": 0.12,
    "bull": "...",
    "bear": "...",
    "verdict": "..."
  }
}
```

Errors:

- `404` if run not found

## POST /api/run

Starts a new background pipeline run.

Request body:

```json
{
  "topic": "Will the Fed cut rates before July 2026?"
}
```

Response:

```json
{
  "run_id": "uuid",
  "topic": "Will the Fed cut rates before July 2026?",
  "started_at": "2026-04-14T12:00:00+00:00"
}
```

Errors:

- `400` if `topic` is empty

## GET /api/run/{run_id}/stream

Server-Sent Events (SSE) endpoint for live run updates.

Event names:

- `node_start`
- `node_done`
- `complete`
- `error`
- `heartbeat`
- `done`

Typical event payloads:

```json
{ "type": "node_start", "node": "fetch", "message": "Polling RSS feeds..." }
```

```json
{ "type": "node_done", "node": "parse", "count": 3, "signals": [ ... ] }
```

```json
{ "type": "complete", "confidence": 0.71, "edge": 0.16, "position": "YES" }
```

Notes:

- Heartbeats are sent approximately every 60s when idle.
- Stream closes with `done` event when run finishes.
