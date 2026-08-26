"""Tests for pipeline/db.py — stdlib unittest, in-memory sqlite (no fixtures)."""

from __future__ import annotations

import sqlite3
import time
import unittest

from pipeline import db


def _mem_conn() -> sqlite3.Connection:
    """Fresh in-memory DB with the production schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


def _seed_seen(conn, url_hash: str, status: str = "new",
               url: str | None = None, source: str = "test",
               title: str | None = None, first_seen_at: str | None = None) -> None:
    """Insert one seen_urls row at a controlled timestamp."""
    conn.execute(
        "INSERT INTO seen_urls (url_hash, url, source, first_seen_at, title, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (url_hash, url or f"https://example.com/{url_hash}", source,
         first_seen_at or "2026-05-21T00:00:00Z", title, status),
    )


class TestDigestWatermark(unittest.TestCase):
    def test_none_when_never_run(self):
        self.assertIsNone(db.get_last_digest_run(_mem_conn()))

    def test_returns_latest_run(self):
        conn = _mem_conn()
        db.record_digest_run(conn, "2026-06-15T07:00:00Z", 3)
        db.record_digest_run(conn, "2026-06-16T07:00:00Z", 0)
        self.assertEqual(db.get_last_digest_run(conn), "2026-06-16T07:00:00Z")

    def test_record_is_idempotent_on_same_timestamp(self):
        conn = _mem_conn()
        db.record_digest_run(conn, "2026-06-16T07:00:00Z", 1)
        db.record_digest_run(conn, "2026-06-16T07:00:00Z", 5)  # REPLACE, not dup
        rows = conn.execute("SELECT ran_at, lead_count FROM digest_runs").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lead_count"], 5)


class TestGetUnprocessedUrls(unittest.TestCase):
    def test_empty_db_returns_no_rows(self):
        conn = _mem_conn()
        self.assertEqual(list(db.get_unprocessed_urls(conn)), [])

    def test_returns_new_status_rows(self):
        conn = _mem_conn()
        _seed_seen(conn, "h1", status="new")
        rows = db.get_unprocessed_urls(conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url_hash"], "h1")

    def test_returns_extracted_status_rows(self):
        """Articles in 'extracted' state crashed mid-push — must be re-attempted."""
        conn = _mem_conn()
        _seed_seen(conn, "h_extracted", status="extracted")
        rows = db.get_unprocessed_urls(conn)
        self.assertEqual([r["url_hash"] for r in rows], ["h_extracted"])

    def test_skips_reported_pushed_filtered_failed(self):
        """Terminal states stay terminal; sweep does not retry them."""
        conn = _mem_conn()
        _seed_seen(conn, "h_reported", status="reported")
        _seed_seen(conn, "h_pushed", status="pushed")
        _seed_seen(conn, "h_filtered", status="filtered")
        _seed_seen(conn, "h_failed", status="failed")
        self.assertEqual(list(db.get_unprocessed_urls(conn)), [])

    def test_orders_oldest_first(self):
        """Older URLs come back first so the slice-to-max doesn't starve them."""
        conn = _mem_conn()
        _seed_seen(conn, "h_new", status="new",
                   first_seen_at="2026-05-21T10:00:00Z")
        _seed_seen(conn, "h_old", status="new",
                   first_seen_at="2026-05-20T10:00:00Z")
        _seed_seen(conn, "h_mid", status="extracted",
                   first_seen_at="2026-05-20T18:00:00Z")
        rows = db.get_unprocessed_urls(conn)
        self.assertEqual(
            [r["url_hash"] for r in rows],
            ["h_old", "h_mid", "h_new"],
        )

    def test_returns_url_source_title_too(self):
        conn = _mem_conn()
        _seed_seen(conn, "h1", status="new",
                   url="https://example.com/abc",
                   source="google_news_az_cre",
                   title="Tempe retail tower")
        row = db.get_unprocessed_urls(conn)[0]
        self.assertEqual(row["url"], "https://example.com/abc")
        self.assertEqual(row["source"], "google_news_az_cre")
        self.assertEqual(row["title"], "Tempe retail tower")


if __name__ == "__main__":
    unittest.main()
