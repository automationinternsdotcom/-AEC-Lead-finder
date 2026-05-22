"""SQLite connection + helpers for sources / seen_urls / articles / runs.

Reuses: sqlite3 (stdlib, Row factory + INSERT OR IGNORE / REPLACE / ON CONFLICT
        for upsert/dedup at the SQL layer), util.utc_now_iso for all timestamps.
Extend: the SCHEMA constant for DDL changes; add a thin helper per new write op.
Schema invariants: all timestamps are utc_now_iso strings; seen_urls.status ∈
  {new, extracted, filtered, pushed, failed}; runs.status ∈ {in_progress, ok, failed}.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline.util import utc_now_iso

DB_PATH = Path(__file__).resolve().parent.parent / "db.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  name        TEXT PRIMARY KEY,
  method      TEXT NOT NULL,
  endpoint    TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  last_synced TEXT
);

CREATE TABLE IF NOT EXISTS seen_urls (
  url_hash      TEXT PRIMARY KEY,
  url           TEXT NOT NULL,
  source        TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  title         TEXT,
  status        TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_seen_urls_status ON seen_urls(status);
CREATE INDEX IF NOT EXISTS idx_seen_urls_source ON seen_urls(source);

CREATE TABLE IF NOT EXISTS articles (
  url_hash            TEXT PRIMARY KEY REFERENCES seen_urls(url_hash),
  extracted_json      TEXT NOT NULL,
  est_deal_size_usd   INTEGER,
  lead_gap            INTEGER NOT NULL DEFAULT 0,
  pipedrive_org_id    INTEGER,
  pipedrive_person_id INTEGER,
  pipedrive_deal_id   INTEGER,
  pushed_at           TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  started_at      TEXT PRIMARY KEY,
  finished_at     TEXT,
  status          TEXT NOT NULL DEFAULT 'in_progress',
  sources_ok      INTEGER NOT NULL DEFAULT 0,
  sources_failed  INTEGER NOT NULL DEFAULT 0,
  articles_seen   INTEGER NOT NULL DEFAULT 0,
  articles_new    INTEGER NOT NULL DEFAULT 0,
  articles_az     INTEGER NOT NULL DEFAULT 0,
  articles_pushed INTEGER NOT NULL DEFAULT 0,
  duration_sec    INTEGER,
  error_summary   TEXT
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the DB and ensure the schema exists. Callers manage transactions."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_run(conn: sqlite3.Connection, started_at: str) -> None:
    conn.execute("INSERT INTO runs (started_at) VALUES (?)", (started_at,))


def finalize_run(
    conn: sqlite3.Connection, started_at: str, counters: dict,
    status: str, duration_sec: int, error: str | None = None,
) -> None:
    """Stamp finished_at + status + counters onto the runs row from insert_run()."""
    conn.execute(
        """UPDATE runs SET finished_at=?, status=?, duration_sec=?, error_summary=?,
                           sources_ok=?, sources_failed=?, articles_seen=?,
                           articles_new=?, articles_az=?, articles_pushed=?
            WHERE started_at=?""",
        (
            utc_now_iso(), status, duration_sec, error,
            counters.get("sources_ok", 0), counters.get("sources_failed", 0),
            counters.get("articles_seen", 0), counters.get("articles_new", 0),
            counters.get("articles_az", 0), counters.get("articles_pushed", 0),
            started_at,
        ),
    )


def sync_source(
    conn: sqlite3.Connection, name: str, method: str, endpoint: str, enabled: bool,
) -> None:
    """UPSERT one sources.yaml entry into the sources table."""
    conn.execute(
        "INSERT INTO sources (name, method, endpoint, enabled, last_synced) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET method=excluded.method, "
        "endpoint=excluded.endpoint, enabled=excluded.enabled, "
        "last_synced=excluded.last_synced",
        (name, method, endpoint, int(bool(enabled)), utc_now_iso()),
    )


def record_seen(
    conn: sqlite3.Connection,
    url_hash: str, url: str, source: str, title: str | None,
) -> bool:
    """INSERT a seen_urls row. Returns True iff this URL was novel (dedup gate)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO seen_urls (url_hash, url, source, first_seen_at, title) "
        "VALUES (?, ?, ?, ?, ?)",
        (url_hash, url, source, utc_now_iso(), title),
    )
    return cur.rowcount > 0


def mark_seen_status(conn: sqlite3.Connection, url_hash: str, status: str) -> None:
    conn.execute("UPDATE seen_urls SET status=? WHERE url_hash=?", (status, url_hash))


def upsert_article(
    conn: sqlite3.Connection, url_hash: str, extracted_json: str,
    est_value: int | None, lead_gap: bool,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO articles "
        "(url_hash, extracted_json, est_deal_size_usd, lead_gap) "
        "VALUES (?, ?, ?, ?)",
        (url_hash, extracted_json, est_value, int(bool(lead_gap))),
    )


def attach_pipedrive_ids(
    conn: sqlite3.Connection, url_hash: str,
    org_id: int, person_id: int | None, deal_id: int,
) -> None:
    """Wire Pipedrive entity IDs onto the articles row and stamp pushed_at."""
    conn.execute(
        "UPDATE articles SET pipedrive_org_id=?, pipedrive_person_id=?, "
        "pipedrive_deal_id=?, pushed_at=? WHERE url_hash=?",
        (org_id, person_id, deal_id, utc_now_iso(), url_hash),
    )
