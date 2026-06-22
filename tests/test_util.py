"""Tests for pipeline/util.py — focusing on log_event's output stream.

Background: log_event used to write to stdout via print(), which corrupted
the JSON output of every CLI tool that share a stdout pipe with internal
modules calling log_event (notably pipeline.cli.fetch via
fetch.discover_new_urls). Tests caught nothing because discover_new_urls
was mocked. Live e2e surfaced the bug.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from unittest.mock import patch

from pipeline import util


class TestLogEvent(unittest.TestCase):
    def test_writes_to_stderr_not_stdout(self):
        """log_event must NOT contaminate stdout — downstream CLI consumers
        pipe stdout straight into json/jq."""
        with patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            util.log_event("source_fetched", source="x", total=5, new=2)
        self.assertEqual(stdout.getvalue(), "",
                         "log_event leaked to stdout — would corrupt CLI JSON output")
        self.assertIn("source_fetched", stderr.getvalue())

    def test_emits_single_json_line(self):
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            util.log_event("article_pushed", url="https://x.com", deal_id=42)
        line = stderr.getvalue().strip()
        # Should be exactly one line of valid JSON
        self.assertNotIn("\n", line)
        parsed = json.loads(line)
        self.assertEqual(parsed["event"], "article_pushed")
        self.assertEqual(parsed["url"], "https://x.com")
        self.assertEqual(parsed["deal_id"], 42)


class TestFetchCliStdoutIntegrity(unittest.TestCase):
    """Regression: fetch CLI stdout must be a single parseable JSON array,
    even when discover_new_urls fires log_event internally."""

    def test_stdout_is_pure_json_even_when_log_event_fires(self):
        """Patch discover_new_urls to BOTH return fresh URLs AND call log_event
        — same as the real implementation does. Stdout must still parse cleanly."""
        import sqlite3
        from pipeline import db
        from pipeline.cli import fetch as fetch_cli
        from pipeline.fetch import NewArticle

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)

        def fake_discover(c, spec=None):
            util.log_event("source_fetched", source="test_source", total=1, new=1)
            return [NewArticle("https://example.com/a", "h_a", "src", "T", None)]

        with patch("pipeline.cli.fetch.db.connect", return_value=conn), \
             patch("pipeline.cli.fetch.fetch.discover_new_urls", side_effect=fake_discover), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             patch("sys.stderr", new_callable=io.StringIO):
            fetch_cli.main()

        # Stdout must be a single valid JSON document. If log_event is on
        # stdout, json.loads will raise "Extra data" or similar.
        data = json.loads(stdout.getvalue())
        self.assertEqual([d["url_hash"] for d in data], ["h_a"])


if __name__ == "__main__":
    unittest.main()
