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
    def test_rss_entry_is_resolved_before_dedup_and_output(self):
        conn = _mem_conn()
        client = MagicMock()
        client.get.return_value.content = b"<rss />"
        client.get.return_value.raise_for_status.return_value = None
        raw = "https://azbex.com/article/project/?utm_source=email&utm_medium=rss"
        publisher = "https://azbex.com/article/project/?utm_source=email&utm_medium=rss"
        expected = "https://azbex.com/article/project"

        parsed = SimpleNamespace(entries=[
            SimpleNamespace(link=raw, title="Project announced"),
        ])

        with patch.object(fetch.feedparser, "parse", return_value=parsed), \
             patch.object(fetch.extract, "resolve_article_url", return_value=publisher):
            fresh = fetch._fetch_feed(client, "azbex", "https://feed.test", conn)

        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].url, expected)
        self.assertEqual(fresh[0].url_hash, util.sha256_hex(expected))

        row = conn.execute("SELECT url_hash, url FROM seen_urls").fetchone()
        self.assertEqual(row["url"], expected)
        self.assertEqual(row["url_hash"], util.sha256_hex(expected))

    def test_unresolvable_rss_entry_is_skipped_not_stored(self):
        conn = _mem_conn()
        client = MagicMock()
        client.get.return_value.content = b"<rss />"
        client.get.return_value.raise_for_status.return_value = None
        raw = "https://publisher.test/bad"
        parsed = SimpleNamespace(entries=[SimpleNamespace(link=raw, title="Bad")])

        with patch.object(fetch.feedparser, "parse", return_value=parsed), \
             patch.object(
                 fetch.extract, "resolve_article_url",
                 side_effect=extract.ExtractError("resolve_failed: no match"),
             ):
            fresh = fetch._fetch_feed(client, "publisher", "https://feed.test", conn)

        self.assertEqual(fresh, [])
        self.assertIsNone(conn.execute("SELECT url FROM seen_urls").fetchone())

    def test_website_scrape_keeps_only_same_site_article_links(self):
        conn = _mem_conn()
        client = MagicMock()
        client.get.return_value.text = """
          <html><body>
            <a href="/about">About us</a>
            <a href="https://news.google.com/rss/articles/CBMi-x">Google wrapper</a>
            <a href="/2026/08/26/mesa-industrial-park-breaks-ground/?utm_source=x">
              Mesa industrial park breaks ground near Loop 202
            </a>
            <a href="/wp-content/logo.png">Logo</a>
            <a href="/2026/08/26/mesa-industrial-park-breaks-ground/?utm_medium=y">
              Duplicate link
            </a>
          </body></html>
        """
        client.get.return_value.raise_for_status.return_value = None

        fresh = fetch._scrape_website(
            client, "az_business", "https://example.com/news/business", conn,
        )

        self.assertEqual(len(fresh), 1)
        self.assertEqual(
            fresh[0].url,
            "https://example.com/2026/08/26/mesa-industrial-park-breaks-ground",
        )
        self.assertEqual(fresh[0].published_at.isoformat(), "2026-08-26")


if __name__ == "__main__":
    unittest.main()
