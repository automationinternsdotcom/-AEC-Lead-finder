"""Tests for pipeline.cli.cache_write — write a Lead to the enriched_orgs cache.

Background: the skill markdown previously embedded an inline Python heredoc
that interpolated the company name into Python source — broke on names with
double-quotes. This CLI replaces that pattern: company name + source come in
as argv (bash quotes them correctly), Lead JSON comes in on stdin.
"""

from __future__ import annotations

import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from pipeline import db
from pipeline.cli import cache_write
from pipeline.enrich import Lead


class _ConnWrapper:
    """Wraps a sqlite3.Connection with a no-op close() so the test can re-read
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
    return _ConnWrapper(conn)


def _lead_json() -> str:
    return json.dumps({
        "name": "Jane Doe", "title": "VP Ops",
        "email": "jane@acme.com", "phone": None,
        "linkedin_url": None, "seniority": "vp", "apollo_id": "grok",
    })


class TestCacheWriteCli(unittest.TestCase):
    def test_writes_and_returns_zero(self):
        conn = _mem_conn()
        with patch("pipeline.cli.cache_write.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "Acme LLC", "grok"]), \
             patch("sys.stdin", io.StringIO(_lead_json())):
            rc = cache_write.main()
        self.assertEqual(rc, 0)

        cached = db.get_cached_enrichment(conn, "Acme LLC")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.name, "Jane Doe")
        self.assertEqual(cached.apollo_id, "grok")

    def test_handles_company_name_with_double_quotes(self):
        """The original heredoc broke on this — CLI argv handles it cleanly."""
        conn = _mem_conn()
        weird_name = 'Some "Quoted" Co'
        with patch("pipeline.cli.cache_write.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", weird_name, "grok"]), \
             patch("sys.stdin", io.StringIO(_lead_json())):
            rc = cache_write.main()
        self.assertEqual(rc, 0)
        cached = db.get_cached_enrichment(conn, weird_name)
        self.assertIsNotNone(cached)

    def test_handles_company_name_with_apostrophe(self):
        conn = _mem_conn()
        with patch("pipeline.cli.cache_write.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "Trader Joe's", "grok"]), \
             patch("sys.stdin", io.StringIO(_lead_json())):
            cache_write.main()
        self.assertIsNotNone(db.get_cached_enrichment(conn, "Trader Joe's"))

    def test_exits_2_on_missing_args(self):
        with patch("sys.argv", ["prog", "only one arg"]), patch("sys.stderr"):
            self.assertEqual(cache_write.main(), 2)
        with patch("sys.argv", ["prog"]), patch("sys.stderr"):
            self.assertEqual(cache_write.main(), 2)

    def test_exits_2_on_invalid_lead_json(self):
        conn = _mem_conn()
        with patch("pipeline.cli.cache_write.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "Acme LLC", "grok"]), \
             patch("sys.stdin", io.StringIO("not json")), \
             patch("sys.stderr"):
            self.assertEqual(cache_write.main(), 2)

    def test_exits_2_on_lead_json_missing_required_field(self):
        conn = _mem_conn()
        # Missing all the dataclass fields except name
        with patch("pipeline.cli.cache_write.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "Acme LLC", "grok"]), \
             patch("sys.stdin", io.StringIO('{"name":"Jane"}')), \
             patch("sys.stderr"):
            self.assertEqual(cache_write.main(), 2)


if __name__ == "__main__":
    unittest.main()
