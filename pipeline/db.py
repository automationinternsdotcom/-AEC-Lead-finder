from __future__ import annotations

import sqlite3
from pathlib import Path

from schema import FetchedPage, LeadRecord
from pipeline.util import utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS fetched_pages (
  url_hash TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  first_seen_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lead_records (
  lead_id TEXT PRIMARY KEY,
  source_url TEXT NOT NULL,
  dedupe_key TEXT,
  qualification_status TEXT NOT NULL,
  pipedrive_status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_source_url ON lead_records(source_url);
CREATE INDEX IF NOT EXISTS idx_leads_dedupe_key ON lead_records(dedupe_key);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def start_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "INSERT INTO runs (run_id, started_at, status) VALUES (?, ?, ?)",
        (run_id, utc_now_iso(), "running"),
    )


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, error=? WHERE run_id=?",
        (utc_now_iso(), status, error, run_id),
    )


def has_seen_url(conn: sqlite3.Connection, url_hash: str) -> bool:
    row = conn.execute("SELECT 1 FROM fetched_pages WHERE url_hash=?", (url_hash,)).fetchone()
    return row is not None


def save_fetched_page(conn: sqlite3.Connection, url_hash: str, page: FetchedPage) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO fetched_pages
          (url_hash, source_id, url, title, first_seen_at, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            url_hash,
            page.source_id,
            page.url,
            page.title,
            page.first_seen_at.isoformat(),
            page.model_dump_json(),
        ),
    )


def save_lead(conn: sqlite3.Connection, lead: LeadRecord) -> None:
    conn.execute(
        """
        INSERT INTO lead_records
          (lead_id, source_url, dedupe_key, qualification_status, pipedrive_status, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(lead_id) DO UPDATE SET
          source_url=excluded.source_url,
          dedupe_key=excluded.dedupe_key,
          qualification_status=excluded.qualification_status,
          pipedrive_status=excluded.pipedrive_status,
          payload_json=excluded.payload_json,
          updated_at=excluded.updated_at
        """,
        (
            lead.lead_id,
            lead.source_url,
            lead.dedupe_key,
            lead.qualification_status,
            lead.pipedrive_status,
            lead.model_dump_json(),
            utc_now_iso(),
        ),
    )


def find_lead_by_source_url(conn: sqlite3.Connection, source_url: str) -> LeadRecord | None:
    row = conn.execute(
        "SELECT payload_json FROM lead_records WHERE source_url=? LIMIT 1",
        (source_url,),
    ).fetchone()
    return LeadRecord.model_validate_json(row["payload_json"]) if row else None


def find_lead_by_dedupe_key(conn: sqlite3.Connection, dedupe_key: str) -> LeadRecord | None:
    row = conn.execute(
        "SELECT payload_json FROM lead_records WHERE dedupe_key=? LIMIT 1",
        (dedupe_key,),
    ).fetchone()
    return LeadRecord.model_validate_json(row["payload_json"]) if row else None


def load_lead(conn: sqlite3.Connection, lead_id: str) -> LeadRecord | None:
    row = conn.execute(
        "SELECT payload_json FROM lead_records WHERE lead_id=?",
        (lead_id,),
    ).fetchone()
    return LeadRecord.model_validate_json(row["payload_json"]) if row else None

