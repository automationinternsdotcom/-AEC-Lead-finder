"""Tests for pipeline.cli.enrich — Apollo lookup JSON."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pipeline import enrich
from pipeline.cli import enrich as enrich_cli


class TestEnrichCli(unittest.TestCase):
    def test_prints_json_when_lead_found(self):
        lead = enrich.Lead(
            name="Jane Doe", title="VP Ops", email="jane@acme.com",
            phone="+15551234567", linkedin_url="https://linkedin.com/in/jane",
            seniority="vp", apollo_id="abc123",
        )
        with patch("pipeline.cli.enrich.config.settings", return_value=None), \
             patch("pipeline.cli.enrich.enrich.find_lead", return_value=lead), \
             patch("sys.argv", ["prog", "acme.com"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = enrich_cli.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["name"], "Jane Doe")
        self.assertEqual(data["email"], "jane@acme.com")

    def test_prints_null_when_no_lead(self):
        with patch("pipeline.cli.enrich.config.settings", return_value=None), \
             patch("pipeline.cli.enrich.enrich.find_lead", return_value=None), \
             patch("sys.argv", ["prog", "acme.com"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            enrich_cli.main()
        self.assertEqual(stdout.getvalue().strip(), "null")


if __name__ == "__main__":
    unittest.main()
