"""Tests for pipeline.cli.dedup_backfill."""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock


def _settings():
    from pipeline import config
    config.settings.cache_clear()
    os.environ.update({
        "PIPEDRIVE_API_TOKEN": "t", "PIPEDRIVE_DOMAIN": "d",
        "PIPEDRIVE_FIELD_ARTICLE_URL": "URLHASH",
        "PIPEDRIVE_FIELD_LEAD_1": "L1", "PIPEDRIVE_FIELD_LEAD_2": "L2",
        "PIPEDRIVE_FIELD_LEAD_3": "L3", "DEDUP_SCORE_THRESHOLD": "0.5",
    })
    return config.settings()


# Two genuine near-duplicates (Jaccard 0.75): tokens {adds,leases,skysong} vs
# {adds,leases,skysong,expansions}. "A" is more complete -> chosen as keeper.
DUP_A = {"id": "a", "title": "SkySong adds 28,000 square feet of new leases and expansions",
         "URLHASH": "ua", "L1": "Jane | CEO", "L2": "Bob | COO",
         "add_time": "2026-05-29 10:00:00", "value": {"amount": 1}, "person_id": 1}
DUP_B = {"id": "b", "title": "SkySong adds 28,000 square feet of new leases",
         "URLHASH": "ub", "L1": "Cara | VP", "add_time": "2026-05-30 10:00:00"}
LONE = {"id": "c", "title": "Creation buys 38-acre site in Avondale",
        "URLHASH": "uc", "add_time": "2026-05-31 10:00:00"}


class TestBackfillDryRun(unittest.TestCase):
    def tearDown(self):
        from pipeline import config
        config.settings.cache_clear()

    def test_dry_run_emits_plan_with_keeper_and_merged_contacts(self):
        _settings()
        from pipeline.cli import dedup_backfill as cli
        with mock.patch.object(cli.email_digest, "make_pipedrive_client"), \
             mock.patch.object(cli.email_digest, "list_raw_leads_since",
                               return_value=[DUP_A, DUP_B, LONE]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.main(["--since", "2026-05-29"])
        self.assertEqual(rc, 0)
        plan = json.loads(out.getvalue())
        # Only the duplicate cluster (>1) appears; lone lead is excluded.
        self.assertEqual(len(plan["clusters"]), 1)
        cl = plan["clusters"][0]
        self.assertEqual(cl["keeper_lead_id"], "a")     # more contacts + fields
        self.assertEqual(cl["delete_lead_ids"], ["b"])
        self.assertIn("Cara | VP", cl["merged_contacts"])  # B's contact carried over
        self.assertEqual(plan["summary"]["leads_deleted"], 1)

    def test_invalid_since_date_returns_2(self):
        _settings()
        from pipeline.cli import dedup_backfill as cli
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            rc = cli.main(["--since", "not-a-date"])
        self.assertEqual(rc, 2)
