"""Tests for main.py — focused on URL collection (the rest of run() needs
mocking 3 external APIs and is left to integration testing)."""

from __future__ import annotations

import sqlite3
import unittest

import main
from pipeline import db
from pipeline.fetch import NewArticle


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


def _na(url_hash: str, source: str = "google_news_az_cre") -> NewArticle:
    return NewArticle(
        url=f"https://example.com/{url_hash}",
        url_hash=url_hash,
        source=source,
        title=f"Article {url_hash}",
        published_at=None,
    )


class TestCollectToProcess(unittest.TestCase):
    def test_returns_only_fresh_when_no_backlog(self):
        conn = _mem_conn()
        fresh = [_na("a"), _na("b")]
        result = main._collect_to_process(conn, lambda _: fresh, max_articles=10)
        self.assertEqual([a.url_hash for a in result], ["a", "b"])

    def test_backlog_comes_before_fresh(self):
        """Older backlog URLs win the slice so they don't get starved."""
        conn = _mem_conn()
        conn.execute(
            "INSERT INTO seen_urls (url_hash, url, source, first_seen_at, title, status) "
            "VALUES ('old1', 'https://example.com/old1', 'src', '2026-05-20T00:00:00Z', 't', 'new')"
        )
        fresh = [_na("new1"), _na("new2")]
        result = main._collect_to_process(conn, lambda _: fresh, max_articles=10)
        self.assertEqual([a.url_hash for a in result], ["old1", "new1", "new2"])

    def test_slices_after_combining(self):
        """The lost-leads bug: ensure slice happens AFTER backlog+fresh, not before."""
        conn = _mem_conn()
        for i in range(3):
            conn.execute(
                "INSERT INTO seen_urls (url_hash, url, source, first_seen_at, title, status) "
                f"VALUES ('old{i}', 'https://example.com/old{i}', 'src', "
                f"'2026-05-20T{i:02d}:00:00Z', 't', 'new')"
            )
        fresh = [_na(f"new{i}") for i in range(3)]
        result = main._collect_to_process(conn, lambda _: fresh, max_articles=4)
        # 3 backlog + 1 fresh — the other 2 fresh would be lost in pre-pivot main.py
        self.assertEqual(
            [a.url_hash for a in result],
            ["old0", "old1", "old2", "new0"],
        )

    def test_backlog_rows_become_NewArticle_with_None_published_at(self):
        """seen_urls doesn't store published_at; backlog NewArticles use None."""
        conn = _mem_conn()
        conn.execute(
            "INSERT INTO seen_urls (url_hash, url, source, first_seen_at, title, status) "
            "VALUES ('b1', 'https://example.com/b1', 'src', '2026-05-20T00:00:00Z', 'T', 'extracted')"
        )
        result = main._collect_to_process(conn, lambda _: [], max_articles=10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].url_hash, "b1")
        self.assertEqual(result[0].url, "https://example.com/b1")
        self.assertEqual(result[0].source, "src")
        self.assertEqual(result[0].title, "T")
        self.assertIsNone(result[0].published_at)


if __name__ == "__main__":
    unittest.main()
