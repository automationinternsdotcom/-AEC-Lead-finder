"""Tests for pipeline.cli.fetch — JSON output of new + backlog URLs."""

from __future__ import annotations

import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from pipeline import db
from pipeline.cli import fetch as fetch_cli
from pipeline.fetch import NewArticle


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


class TestFetchCli(unittest.TestCase):
    def test_prints_json_array_of_urls(self):
        conn = _mem_conn()
        fresh = [
            NewArticle("https://example.com/a", "h_a", "src", "Article A", None),
            NewArticle("https://example.com/b", "h_b", "src", "Article B", None),
        ]
        with patch("pipeline.cli.fetch.db.connect", return_value=conn), \
             patch("pipeline.cli.fetch.fetch.discover_new_urls", return_value=fresh), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = fetch_cli.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(
            [d["url_hash"] for d in data],
            ["h_a", "h_b"],
        )
        self.assertEqual(data[0]["url"], "https://example.com/a")
        self.assertEqual(data[0]["source"], "src")

    def test_includes_backlog_before_fresh(self):
        conn = _mem_conn()
        conn.execute(
            "INSERT INTO seen_urls (url_hash, url, source, first_seen_at, title, status) "
            "VALUES ('h_old', 'https://example.com/old', 'src', '2026-05-20T00:00:00Z', 't', 'new')"
        )
        fresh = [NewArticle("https://example.com/new", "h_new", "src", "T", None)]
        with patch("pipeline.cli.fetch.db.connect", return_value=conn), \
             patch("pipeline.cli.fetch.fetch.discover_new_urls", return_value=fresh), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            fetch_cli.main()
        data = json.loads(stdout.getvalue())
        self.assertEqual([d["url_hash"] for d in data], ["h_old", "h_new"])


if __name__ == "__main__":
    unittest.main()
