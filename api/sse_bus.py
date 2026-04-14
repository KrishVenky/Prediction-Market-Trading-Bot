"""
SSE Event Bus
--------------
Bridges the synchronous pipeline thread with async FastAPI SSE consumers.

Usage from pipeline thread (sync):
    from api.sse_bus import emit_sync
    emit_sync(run_id, {"type": "node_start", "node": "fetch"})

Usage from FastAPI SSE endpoint (async):
    from api.sse_bus import subscribe, unsubscribe
    q = subscribe(run_id)
    event = await q.get()
    unsubscribe(run_id)
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

# run_id → asyncio.Queue
_queues: dict[str, asyncio.Queue] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None
_lock = threading.Lock()

SENTINEL = "__DONE__"


def register_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call this once from the FastAPI startup handler."""
    global _loop
    _loop = loop


def subscribe(run_id: str) -> asyncio.Queue:
    """Create and return a new queue for *run_id*."""
    q: asyncio.Queue = asyncio.Queue()
    with _lock:
        _queues[run_id] = q
    return q


def unsubscribe(run_id: str) -> None:
    with _lock:
        _queues.pop(run_id, None)


def emit_sync(run_id: str, event: dict) -> None:
    """
    Thread-safe emit from the pipeline thread.
    Drops silently if no subscriber or no loop registered.
    """
    if _loop is None:
        return
    with _lock:
        q = _queues.get(run_id)
    if q is None:
        return
    _loop.call_soon_threadsafe(q.put_nowait, event)


def close_sync(run_id: str) -> None:
    """Signal the SSE consumer that the stream is done."""
    emit_sync(run_id, {"type": SENTINEL})
