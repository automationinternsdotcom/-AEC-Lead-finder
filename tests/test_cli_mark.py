"""Tests for pipeline.cli.mark — updates seen_urls.status."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from pipeline import db
from pipeline.cli import mark as mark_cli


class _ConnWrapper:
    """Wraps a real sqlite3.Connection with no-op close() so tests can inspect
    the in-memory DB after the CLI's `finally: conn.close()` would have closed it."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):  # no-op for tests
        pass


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    conn.execute(
        "INSERT INTO seen_urls (url_hash, url, source, first_seen_at, title, status) "
        "VALUES ('h1', 'https://x.com', 'src', '2026-05-21T00:00:00Z', 't', 'new')"
    )
    return _ConnWrapper(conn)


class TestMarkCli(unittest.TestCase):
    def test_updates_status(self):
        conn = _mem_conn()
        with patch("pipeline.cli.mark.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "h1", "pushed"]):
            rc = mark_cli.main()
        self.assertEqual(rc, 0)
        status = conn.execute(
            "SELECT status FROM seen_urls WHERE url_hash='h1'"
        ).fetchone()["status"]
        self.assertEqual(status, "pushed")

    def test_rejects_invalid_status(self):
        conn = _mem_conn()
        with patch("pipeline.cli.mark.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "h1", "bogus"]), \
             patch("sys.stderr"):
            rc = mark_cli.main()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
