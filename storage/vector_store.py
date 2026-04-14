"""
PolySignal  —  ChromaDB Semantic Search
-----------------------------------------
Two indexing paths feed into one collection:

  1. index_signals()      — called after each pipeline run (LLM-filtered signals)
  2. index_raw_articles() — called by feed_indexer.py (all RSS/Twitter articles)

Both paths upsert by ID so re-runs never duplicate.

Collection schema per document:
  id       : stable hash of URL (for dedup) or "<run_id>_<idx>"
  document : "<title>. <signal_text>"   (embedded by ChromaDB default encoder)
  metadata : { run_id, source, url, title, sentiment, trust_score,
               relevance, signal_text, run_topic }

Public API:
  index_signals(run_id, parsed_signals, run_topic)   → None
  index_raw_articles(articles)                        → None
  semantic_search(query, n_results, min_trust,
                  min_similarity)                     → list[dict]
  collection_size()                                   → int
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

# ── ChromaDB ───────────────────────────────────────────────────────────────────
import chromadb
from chromadb.config import Settings

_DB_DIR  = Path(__file__).parent.parent / "chromadb_store"
_COLL    = "polysignal_signals"
_lock    = threading.Lock()

# Lazy-initialised singleton
_client: Optional[chromadb.ClientAPI] = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(_DB_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = _client.get_or_create_collection(
            name=_COLL,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


# ── Index ──────────────────────────────────────────────────────────────────────

def index_signals(run_id: str, parsed_signals: list[dict], run_topic: str = "") -> None:
    """
    Embed and store each parsed signal from a pipeline run.
    Safe to call multiple times — duplicate IDs are upserted.
    """
    if not parsed_signals:
        return

    coll = _get_collection()

    ids       = []
    documents = []
    metadatas = []

    for i, s in enumerate(parsed_signals):
        doc_id = f"{run_id}_{i}"
        # The text that gets embedded — title + signal sentence gives best recall
        doc_text = f"{s.get('title', '')}. {s.get('signal', s.get('signal_text', ''))}"

        ids.append(doc_id)
        documents.append(doc_text.strip())
        metadatas.append({
            "run_id":      run_id,
            "run_topic":   run_topic[:300],
            "source":      s.get("source", ""),
            "url":         s.get("url", ""),
            "title":       s.get("title", "")[:300],
            "sentiment":   s.get("sentiment", "neutral"),
            "trust_score": float(s.get("trust_score", 0)),
            "relevance":   float(s.get("relevance", 5.0)),
            "signal_text": s.get("signal", s.get("signal_text", ""))[:500],
        })

    with _lock:
        coll.upsert(ids=ids, documents=documents, metadatas=metadatas)

    print(f"  [CHROMA] Indexed {len(ids)} signals for run {run_id[:8]}…")


# ── Bulk article index (from feed_indexer) ─────────────────────────────────────

def index_raw_articles(articles: list[dict]) -> None:
    """
    Bulk-upsert raw RSS/Twitter articles into ChromaDB.

    Each article dict must have at minimum:
      id, url, title, text/signal_text, source, trust_score

    Batched in chunks of 200 to avoid memory spikes.
    """
    if not articles:
        return

    coll = _get_collection()
    BATCH = 200

    ids_all       = []
    documents_all = []
    metadatas_all = []

    for a in articles:
        doc_text = f"{a.get('title', '')}. {a.get('signal_text', a.get('text', ''))}"
        ids_all.append(a["id"])
        documents_all.append(doc_text.strip()[:1000])
        metadatas_all.append({
            "run_id":      a.get("run_id", "feed_index"),
            "run_topic":   a.get("run_topic", "")[:300],
            "source":      a.get("source", ""),
            "url":         a.get("url", "")[:500],
            "title":       a.get("title", "")[:300],
            "sentiment":   a.get("sentiment", "neutral"),
            "trust_score": float(a.get("trust_score", 0.55)),
            "relevance":   float(a.get("relevance", 5.0)),
            "signal_text": a.get("signal_text", a.get("text", ""))[:500],
            "tags":        a.get("tags", ""),
        })

    total = 0
    with _lock:
        for i in range(0, len(ids_all), BATCH):
            coll.upsert(
                ids=ids_all[i:i+BATCH],
                documents=documents_all[i:i+BATCH],
                metadatas=metadatas_all[i:i+BATCH],
            )
            total += min(BATCH, len(ids_all) - i)

    print(f"  [CHROMA] Bulk-indexed {total} articles (collection now {coll.count()})")


# ── Search ─────────────────────────────────────────────────────────────────────

# Minimum cosine similarity to be included in results.
# Anything below this is effectively random noise — not genuinely related.
_MIN_SIMILARITY = 0.20

def semantic_search(
    query: str,
    n_results: int = 8,
    min_trust: float = 0.0,
    min_similarity: float = _MIN_SIMILARITY,
) -> list[dict]:
    """
    Find the most semantically similar articles to `query`.

    Filters:
      - min_similarity : drop results below this cosine similarity (noise floor)
      - min_trust      : optional trust score filter

    Returns list sorted by similarity DESC, then trust_score DESC.
    """
    coll = _get_collection()
    if coll.count() == 0:
        return []

    # Over-fetch then filter, so we can apply quality thresholds
    fetch_n = min(max(n_results * 4, 30), coll.count())

    try:
        results = coll.query(
            query_texts=[query],
            n_results=fetch_n,
            include=["metadatas", "distances", "documents"],
        )
    except Exception as exc:
        print(f"  [CHROMA] Search error: {exc}")
        return []

    hits = []
    seen_titles: set[str] = set()

    for meta, dist, doc in zip(
        results["metadatas"][0],
        results["distances"][0],
        results["documents"][0],
    ):
        similarity = round(1 - dist, 4)
        trust      = float(meta.get("trust_score", 0))

        if similarity < min_similarity:
            continue
        if trust < min_trust:
            continue

        # Deduplicate near-identical titles
        title_key = meta.get("title", "")[:60].lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        hits.append({
            **meta,
            "distance":   round(dist, 4),
            "similarity": similarity,
            "document":   doc,
        })

    # Sort: semantic relevance first, then source quality
    hits.sort(key=lambda h: (-h["similarity"], -h["trust_score"]))
    return hits[:n_results]


# ── Stats ──────────────────────────────────────────────────────────────────────

def collection_size() -> int:
    """Return how many signal embeddings are currently stored."""
    try:
        return _get_collection().count()
    except Exception:
        return 0
