"""
SQLite Storage Layer
--------------------
Single source of truth for all pipeline runs, signals, and results.
Uses raw sqlite3 (no ORM) with a threading.Lock for safe writes from
the pipeline threadpool.

Call `init_db()` once at server startup.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "polysignal.db"
_lock = threading.Lock()


# ── Connection factory ─────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")   # better concurrent reads
    return c


# ── Schema init ────────────────────────────────────────────────────────────────

def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id          TEXT PRIMARY KEY,
                topic       TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'running',
                llm_model   TEXT,
                created_at  TEXT NOT NULL,
                finished_at TEXT,
                duration_s  REAL
            );

            CREATE TABLE IF NOT EXISTS raw_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                source      TEXT,
                source_type TEXT DEFAULT 'rss',
                url         TEXT,
                title       TEXT,
                text        TEXT,
                published   TEXT,
                tags        TEXT,
                trust_score REAL,
                scraped_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS parsed_signals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT NOT NULL,
                source       TEXT,
                source_type  TEXT DEFAULT 'rss',
                url          TEXT,
                title        TEXT,
                event        TEXT,
                sentiment    TEXT,
                relevance    REAL,
                signal_text  TEXT,
                trust_score  REAL,
                llm_model    TEXT
            );

            CREATE TABLE IF NOT EXISTS results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT NOT NULL,
                position     TEXT,
                confidence   REAL,
                market_price REAL,
                edge         REAL,
                reasoning    TEXT,
                bull         TEXT,
                bear         TEXT,
                verdict      TEXT,
                final_output TEXT,
                llm_model    TEXT,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT NOT NULL,
                node         TEXT NOT NULL,
                message_type TEXT NOT NULL,
                toml_payload TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_index (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name   TEXT NOT NULL,
                source_type   TEXT NOT NULL DEFAULT 'rss',
                feed_url      TEXT,
                trust_score   REAL,
                article_count INTEGER DEFAULT 0,
                last_scraped  TEXT,
                UNIQUE(source_name)
            );
        """)


# ── Runs ───────────────────────────────────────────────────────────────────────

def create_run(run_id: str, topic: str, created_at: str) -> None:
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO runs (id, topic, status, created_at) VALUES (?,?,?,?)",
            (run_id, topic, "running", created_at),
        )


def finish_run(run_id: str, status: str, finished_at: str) -> None:
    with _lock, _conn() as c:
        c.execute(
            "UPDATE runs SET status=?, finished_at=? WHERE id=?",
            (status, finished_at, run_id),
        )


def get_runs(limit: int = 20) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


# ── Raw signals ────────────────────────────────────────────────────────────────

def insert_raw_signals(run_id: str, signals: list, scraped_at: str) -> None:
    rows = [
        (
            run_id,
            s.source,
            s.url,
            s.title,
            s.text[:500],
            s.published.isoformat() if s.published else None,
            ",".join(s.tags),
            scraped_at,
        )
        for s in signals
    ]
    with _lock, _conn() as c:
        c.executemany(
            "INSERT INTO raw_signals (run_id,source,url,title,text,published,tags,scraped_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def get_raw_signals(run_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM raw_signals WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Parsed signals ─────────────────────────────────────────────────────────────

def insert_parsed_signals(run_id: str, signals: list[dict]) -> None:
    rows = [
        (
            run_id,
            s.get("source"),
            s.get("url"),
            s.get("title"),
            s.get("event"),
            s.get("sentiment"),
            s.get("relevance"),
            s.get("signal"),
            s.get("trust_score"),
        )
        for s in signals
    ]
    with _lock, _conn() as c:
        c.executemany(
            "INSERT INTO parsed_signals "
            "(run_id,source,url,title,event,sentiment,relevance,signal_text,trust_score) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )


def get_parsed_signals(run_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM parsed_signals WHERE run_id=? ORDER BY trust_score DESC",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Results ────────────────────────────────────────────────────────────────────

def insert_result(run_id: str, state: dict, created_at: str) -> None:
    debate = state.get("debate_result", {})
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO results "
            "(run_id,position,confidence,market_price,edge,reasoning,bull,bear,verdict,final_output,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                debate.get("position"),
                state.get("confidence_score"),
                0.5,                        # market_price not yet in state — default 0.5
                state.get("edge"),
                "",
                debate.get("bull", "")[:800],
                debate.get("bear", "")[:800],
                debate.get("verdict", "")[:800],
                state.get("final_output", "")[:3000],
                created_at,
            ),
        )


def get_result(run_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM results WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


# ── Agent messages (TOML inter-agent payloads) ─────────────────────────────────

def insert_agent_message(run_id: str, node: str, message_type: str, toml_payload: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO agent_messages (run_id, node, message_type, toml_payload, created_at) "
            "VALUES (?,?,?,?,?)",
            (run_id, node, message_type, toml_payload, now),
        )


def get_agent_messages(run_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM agent_messages WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Source index ───────────────────────────────────────────────────────────────

def upsert_source(source_name: str, source_type: str, feed_url: str, trust_score: float) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _conn() as c:
        c.execute("""
            INSERT INTO source_index (source_name, source_type, feed_url, trust_score, article_count, last_scraped)
            VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(source_name) DO UPDATE SET
                feed_url     = excluded.feed_url,
                trust_score  = excluded.trust_score,
                last_scraped = excluded.last_scraped
        """, (source_name, source_type, feed_url, trust_score, now))


def increment_source_count(source_name: str, count: int = 1) -> None:
    with _lock, _conn() as c:
        c.execute(
            "UPDATE source_index SET article_count = article_count + ? WHERE source_name = ?",
            (count, source_name),
        )


def get_source_stats() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM source_index ORDER BY trust_score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_pipeline_stats() -> dict:
    """Aggregate stats for the dashboard data-lineage panel."""
    with _conn() as c:
        total_runs      = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        complete_runs   = c.execute("SELECT COUNT(*) FROM runs WHERE status='complete'").fetchone()[0]
        total_raw       = c.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
        total_parsed    = c.execute("SELECT COUNT(*) FROM parsed_signals").fetchone()[0]
        total_results   = c.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        sources_by_count = c.execute("""
            SELECT source, source_type, COUNT(*) as cnt,
                   AVG(trust_score) as avg_trust
            FROM raw_signals
            GROUP BY source
            ORDER BY cnt DESC
            LIMIT 20
        """).fetchall()
        sentiment_dist = c.execute("""
            SELECT sentiment, COUNT(*) as cnt
            FROM parsed_signals
            GROUP BY sentiment
        """).fetchall()
    return {
        "total_runs":      total_runs,
        "complete_runs":   complete_runs,
        "total_raw":       total_raw,
        "total_parsed":    total_parsed,
        "total_results":   total_results,
        "sources":         [dict(r) for r in sources_by_count],
        "sentiment_dist":  [dict(r) for r in sentiment_dist],
    }
