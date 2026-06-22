"""Tests for pipeline.cli.fetch — JSON output of new + backlog URLs."""

from __future__ import annotations

import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from pipeline import db
from pipeline.cli import fetch as fetch_cli
from pipeline.fetch import NewArticle, sources_for_campaign
from pipeline.spec import load_campaign_spec


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


class TestCampaignSourceSelection(unittest.TestCase):
    def test_without_spec_uses_enabled_sources_legacy_mode(self):
        registry = [
            {"name": "on", "method": "rss", "endpoint": "https://x.test/feed", "enabled": True},
            {"name": "off", "method": "rss", "endpoint": "https://x.test/off", "enabled": False},
        ]
        selected = sources_for_campaign(None, registry)
        self.assertEqual([s["name"] for s in selected], ["on"])

    def test_cleaning_spec_selects_current_source_set(self):
        spec = load_campaign_spec()
        selected = sources_for_campaign(spec)
        names = {s["name"] for s in selected}
        self.assertEqual(
            names,
            {
                "google_news_az_cre",
                "google_news_phoenix_dev",
                "google_news_tucson_cre",
                "azbex",
                "arizona_digital_free_press",
            },
        )


if __name__ == "__main__":
    unittest.main()
