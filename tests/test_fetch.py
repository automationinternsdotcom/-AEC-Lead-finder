"""Tests for fetch.py URL discovery and canonical identity."""

from __future__ import annotations

import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pipeline import db, extract, fetch, util


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


class TestFetchResolvesArticleUrls(unittest.TestCase):
    def test_google_news_entry_is_resolved_before_dedup_and_output(self):
        conn = _mem_conn()
        client = MagicMock()
        client.get.return_value.content = b"<rss />"
        client.get.return_value.raise_for_status.return_value = None
        wrapper = "https://news.google.com/rss/articles/CBMi-test?oc=5&utm_source=x"
        publisher = "https://azbex.com/article/project/?utm_source=google&utm_medium=rss"
        expected = "https://azbex.com/article/project"

        parsed = SimpleNamespace(entries=[
            SimpleNamespace(link=wrapper, title="Project announced"),
        ])

        with patch.object(fetch.feedparser, "parse", return_value=parsed), \
             patch.object(fetch.extract, "resolve_article_url", return_value=publisher):
            fresh = fetch._fetch_one(client, "google_news_az_cre", "https://feed.test", conn)

        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].url, expected)
        self.assertEqual(fresh[0].url_hash, util.sha256_hex(expected))

        row = conn.execute("SELECT url_hash, url FROM seen_urls").fetchone()
        self.assertEqual(row["url"], expected)
        self.assertEqual(row["url_hash"], util.sha256_hex(expected))

    def test_unresolvable_google_news_entry_is_skipped_not_stored(self):
        conn = _mem_conn()
        client = MagicMock()
        client.get.return_value.content = b"<rss />"
        client.get.return_value.raise_for_status.return_value = None
        wrapper = "https://news.google.com/rss/articles/CBMi-bad?oc=5"
        parsed = SimpleNamespace(entries=[SimpleNamespace(link=wrapper, title="Bad")])

        with patch.object(fetch.feedparser, "parse", return_value=parsed), \
             patch.object(
                 fetch.extract, "resolve_article_url",
                 side_effect=extract.ExtractError("gnews_decode_failed: no match"),
             ):
            fresh = fetch._fetch_one(client, "google_news_az_cre", "https://feed.test", conn)

        self.assertEqual(fresh, [])
        self.assertIsNone(conn.execute("SELECT url FROM seen_urls").fetchone())


if __name__ == "__main__":
    unittest.main()
