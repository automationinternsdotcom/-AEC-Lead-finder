"""Tests for pipeline.cli.grok_parse — stdin (Grok text) → stdout (Lead JSON or null)."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pipeline.cli import grok_parse as cli_grok_parse


SPIKE_RESPONSE = """1. Michael Wilson
Current Title: Chief Operating Officer (COO), Mark-Taylor, Inc.
LinkedIn: https://www.linkedin.com/in/michael-wilson-2a982625a
Professional Email: Likely michael.wilson@mark-taylor.com"""


class TestGrokParseCli(unittest.TestCase):
    def test_prints_lead_json_on_match(self):
        with patch("sys.stdin", io.StringIO(SPIKE_RESPONSE)), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cli_grok_parse.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["name"], "Michael Wilson")
        self.assertEqual(data["email"], "michael.wilson@mark-taylor.com")
        self.assertEqual(data["seniority"], "c_suite")
        self.assertEqual(data["apollo_id"], "grok")

    def test_prints_null_when_no_match(self):
        with patch("sys.stdin", io.StringIO("Sorry, no results.")), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cli_grok_parse.main()
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().strip(), "null")


if __name__ == "__main__":
    unittest.main()
