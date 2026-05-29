"""Tests for pipeline.cli.cache_lookup — org-cache hit/miss as Lead JSON."""

from __future__ import annotations

import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from pipeline import db
from pipeline.cli import cache_lookup
from pipeline.enrich import Lead


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


class TestCacheLookupCli(unittest.TestCase):
    def test_prints_null_on_miss(self):
        conn = _mem_conn()
        with patch("pipeline.cli.cache_lookup.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "Unknown Co"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cache_lookup.main()
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().strip(), "null")

    def test_prints_lead_json_on_hit(self):
        conn = _mem_conn()
        lead = Lead(name="Jane", title="COO", email="j@x.com", phone=None,
                    linkedin_url=None, seniority="c_suite", apollo_id="grok")
        db.cache_enrichment(conn, "Acme LLC", lead, source="grok")
        conn.commit()
        with patch("pipeline.cli.cache_lookup.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "acme"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cache_lookup.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["name"], "Jane")
        self.assertEqual(data["apollo_id"], "grok")

    def test_exits_2_on_missing_arg(self):
        with patch("sys.argv", ["prog"]), patch("sys.stderr"):
            rc = cache_lookup.main()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
