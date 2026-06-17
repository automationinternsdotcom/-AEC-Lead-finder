"""SQLite connection + helpers for sources / seen_urls / articles / runs.

Reuses: sqlite3 (stdlib, Row factory + INSERT OR IGNORE / REPLACE / ON CONFLICT
        for upsert/dedup at the SQL layer), util.utc_now_iso for all timestamps.
Extend: the SCHEMA constant for DDL changes; add a thin helper per new write op.
Schema invariants: all timestamps are utc_now_iso strings; seen_urls.status ∈
  {new, extracted, filtered, pushed, failed, merged}; runs.status ∈ {in_progress, ok, failed}.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
from pathlib import Path

from pipeline.enrich import Lead
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

CREATE TABLE IF NOT EXISTS enriched_orgs (
  name_normalized TEXT PRIMARY KEY,
  raw_name        TEXT NOT NULL,
  lead_json       TEXT NOT NULL,
  source          TEXT NOT NULL,
  enriched_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enriched_orgs_enriched_at ON enriched_orgs(enriched_at);

CREATE TABLE IF NOT EXISTS digest_runs (
  ran_at     TEXT PRIMARY KEY,
  lead_count INTEGER NOT NULL DEFAULT 0
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


def get_unprocessed_urls(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Backlog: seen_urls rows that never reached a terminal state.

    Returns rows with status IN ('new', 'extracted') ordered oldest-first,
    so the orchestrator's slice-to-max_articles doesn't starve older URLs.

    - 'new'       = recorded by fetch but never extracted (over-sliced on a
                    prior run, or fetch returned more than max_articles).
    - 'extracted' = extracted but never pushed (process crashed between
                    push.sync_to_pipedrive and mark_seen_status('pushed')).

    Terminal states ('pushed', 'filtered', 'failed') are excluded — operators
    can recover 'failed' rows manually.
    """
    return conn.execute(
        "SELECT url_hash, url, source, title, first_seen_at FROM seen_urls "
        "WHERE status IN ('new', 'extracted') ORDER BY first_seen_at"
    ).fetchall()


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


# ── enriched_orgs cache: per-org Lead memoization ──────────────────────────────
#
# Same company appearing in multiple articles should reuse the same Grok/Apollo
# enrichment instead of paying for it twice. The cache key is a normalized
# version of the org name (lowercase, business-suffixes stripped, punctuation
# collapsed) so 'Mark-Taylor Residential LLC' and 'mark taylor residential'
# resolve to the same row.

_BUSINESS_SUFFIXES = (
    "llc", "l.l.c.", "l l c",
    "inc", "inc.", "incorporated",
    "corp", "corp.", "corporation",
    "ltd", "ltd.", "limited",
    "lp", "l.p.",
    "llp", "l.l.p.",
    "company", "co", "co.",
)
_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in _BUSINESS_SUFFIXES) + r")\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_MULTI_SPACE = re.compile(r"\s+")


def _normalize_org_name(name: str) -> str:
    """Lowercase, strip punctuation, drop common business suffixes, collapse whitespace.

    Order matters: punctuation strip BEFORE suffix strip — `L.L.C.` would
    otherwise fail to match the `l.l.c.` suffix because `\\b` doesn't anchor
    between `.` and end-of-string. After punctuation strip it's `l l c`,
    which the suffix list also contains.
    """
    s = name.lower()
    s = _NON_ALNUM.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    s = _SUFFIX_RE.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    return s


def get_cached_enrichment(conn: sqlite3.Connection, org_name: str) -> Lead | None:
    """Return a cached Lead for this org, or None if uncached.

    Lookup key is the normalized form, so 'Mark-Taylor Residential LLC' and
    'mark taylor residential' resolve to the same row.
    """
    row = conn.execute(
        "SELECT lead_json FROM enriched_orgs WHERE name_normalized = ?",
        (_normalize_org_name(org_name),),
    ).fetchone()
    if row is None:
        return None
    return Lead(**json.loads(row["lead_json"]))


def cache_enrichment(conn: sqlite3.Connection, org_name: str,
                     lead: Lead, source: str) -> None:
    """Upsert this enrichment into the cache. Newer writes overwrite older ones."""
    conn.execute(
        "INSERT OR REPLACE INTO enriched_orgs "
        "(name_normalized, raw_name, lead_json, source, enriched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            _normalize_org_name(org_name), org_name,
            json.dumps(dataclasses.asdict(lead)), source, utc_now_iso(),
        ),
    )


# ── digest watermark: when the email digest last sent successfully ─────────────
#
# pipeline.cli.email_digest --daily sends Leads created since the last successful
# digest. We persist that watermark here so a successful send advances it and the
# same Lead is never emailed twice; a failed send leaves it untouched so the next
# run retries. One row per successful run (audit trail); the watermark is MAX(ran_at).


def get_last_digest_run(conn: sqlite3.Connection) -> str | None:
    """utc_now_iso of the most recent successful digest run, or None if never run."""
    row = conn.execute("SELECT MAX(ran_at) AS last FROM digest_runs").fetchone()
    return row["last"] if row and row["last"] else None


def record_digest_run(conn: sqlite3.Connection, ran_at: str, lead_count: int) -> None:
    """Stamp a successful digest run, advancing the watermark to `ran_at`."""
    conn.execute(
        "INSERT OR REPLACE INTO digest_runs (ran_at, lead_count) VALUES (?, ?)",
        (ran_at, lead_count),
    )
