"""Tests for pipeline.cli.find_event_candidates."""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock


class TestFindEventCandidates(unittest.TestCase):
    def setUp(self):
        from pipeline import config
        config.settings.cache_clear()
        os.environ.update({
            "PIPEDRIVE_API_TOKEN": "t", "PIPEDRIVE_DOMAIN": "d",
            "PIPEDRIVE_FIELD_ARTICLE_URL": "URLHASH",
            "PIPEDRIVE_FIELD_LEAD_1": "L1",
        })

    def tearDown(self):
        from pipeline import config
        config.settings.cache_clear()

    def _run(self, article, raw_leads):
        from pipeline.cli import find_event_candidates as cli
        with mock.patch.object(cli.email_digest, "make_pipedrive_client"), \
             mock.patch.object(cli.email_digest, "list_raw_leads_since",
                               return_value=raw_leads), \
             mock.patch("sys.stdin", io.StringIO(json.dumps(article))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.main()
        return rc, json.loads(out.getvalue())

    def test_returns_scored_same_event_candidate(self):
        # Article title tokens: {'adds', 'leases', 'skysong'}
        # Lead 1 title tokens:  {'adds', 'expansions', 'leases', 'skysong'}
        # Jaccard = 3/4 = 0.75  (>= threshold 0.5)  ✓
        # Lead 2 is unrelated — Jaccard ≈ 0.0
        article = {"title": "SkySong adds 28,000 square feet of new leases",
                   "company_name": "Plaza Companies", "city": "Scottsdale",
                   "signal_type": "lease"}
        leads = [
            {"id": "1",
             "title": "SkySong adds 28,000 square feet of new leases and expansions",
             "URLHASH": "u1", "L1": "Jane | CEO", "add_time": "2026-06-10 00:00:00"},
            {"id": "2", "title": "Creation buys 38-acre site in Avondale",
             "URLHASH": "u2", "add_time": "2026-06-10 00:00:00"},
        ]
        rc, payload = self._run(article, leads)
        self.assertEqual(rc, 0)
        ids = [c["lead_id"] for c in payload]
        self.assertIn("1", ids)         # same event surfaced
        self.assertNotIn("2", ids)      # unrelated filtered out

    def test_pipedrive_error_fails_open_to_empty(self):
        from pipeline.cli import find_event_candidates as cli
        article = {"title": "x", "company_name": "y", "city": None, "signal_type": "other"}
        with mock.patch.object(cli.email_digest, "make_pipedrive_client",
                               side_effect=RuntimeError("boom")), \
             mock.patch("sys.stdin", io.StringIO(json.dumps(article))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.main()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), [])
