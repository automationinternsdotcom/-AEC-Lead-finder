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


class TestBackfillApply(unittest.TestCase):
    def tearDown(self):
        from pipeline import config
        config.settings.cache_clear()
        os.environ.pop("DRY_RUN", None)

    def test_apply_merges_then_deletes_and_skips_on_merge_failure(self):
        _settings()
        from pipeline.cli import dedup_backfill as cli
        plan = {"clusters": [
            {"keeper_lead_id": "a", "merged_contacts": ["Jane | CEO"],
             "delete_lead_ids": ["b"], "delete_urls": ["ub"], "overflow": []},
            {"keeper_lead_id": "x", "merged_contacts": ["Z | CTO"],
             "delete_lead_ids": ["y"], "delete_urls": ["uy"], "overflow": []},
        ], "summary": {"clusters": 2, "leads_deleted": 2}}

        pd = mock.MagicMock()
        order = []

        def fake_patch(resource, lead_id, payload):
            order.append(("patch", lead_id))
            if lead_id != "a":               # second cluster's merge fails
                raise RuntimeError("patch boom")

        def fake_delete(resource, lead_id):
            order.append(("delete", lead_id))

        pd.patch.side_effect = fake_patch
        pd.delete.side_effect = fake_delete

        with mock.patch.object(cli.push, "PipedriveClient") as PC, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            PC.return_value.__enter__.return_value = pd
            rc = cli._apply(plan, cli.config.settings())
        self.assertEqual(rc, 0)
        # 'a' merged then 'b' deleted; 'x' merge failed so 'y' NOT deleted.
        self.assertIn(("patch", "a"), order)
        self.assertIn(("delete", "b"), order)
        self.assertNotIn(("delete", "y"), order)
        self.assertLess(order.index(("patch", "a")), order.index(("delete", "b")))
        result = json.loads(out.getvalue())
        self.assertEqual(result["leads_deleted"], 1)
        self.assertTrue(result["applied"])   # DRY_RUN unset in _settings()

    def test_apply_posts_overflow_note_before_delete(self):
        _settings()
        from pipeline.cli import dedup_backfill as cli
        plan = {"clusters": [
            {"keeper_lead_id": "a", "merged_contacts": ["A | CEO", "B | COO", "C | VP"],
             "delete_lead_ids": ["b"], "delete_urls": ["ub"],
             "overflow": ["D | Director of Maintenance"]},
        ], "summary": {"clusters": 1, "leads_deleted": 1}}
        pd = mock.MagicMock()
        order = []
        pd.post.side_effect = lambda res, payload: order.append(("post", res))
        pd.delete.side_effect = lambda res, lid: order.append(("delete", lid))
        with mock.patch.object(cli.push, "PipedriveClient") as PC, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            PC.return_value.__enter__.return_value = pd
            rc = cli._apply(plan, cli.config.settings())
        self.assertEqual(rc, 0)
        note_calls = [c for c in pd.post.call_args_list if c.args[0] == "notes"]
        self.assertTrue(any("D | Director of Maintenance" in c.args[1]["content"]
                            for c in note_calls))
        self.assertLess(order.index(("post", "notes")), order.index(("delete", "b")))

    def test_apply_dry_run_writes_nothing_but_reports_would_delete(self):
        from pipeline import config
        config.settings.cache_clear()
        os.environ.update({
            "PIPEDRIVE_API_TOKEN": "t", "PIPEDRIVE_DOMAIN": "d",
            "PIPEDRIVE_FIELD_ARTICLE_URL": "URLHASH",
            "PIPEDRIVE_FIELD_LEAD_1": "L1", "PIPEDRIVE_FIELD_LEAD_2": "L2",
            "PIPEDRIVE_FIELD_LEAD_3": "L3", "DRY_RUN": "1",
        })
        from pipeline.cli import dedup_backfill as cli
        plan = {"clusters": [
            {"keeper_lead_id": "a", "merged_contacts": ["Jane | CEO"],
             "delete_lead_ids": ["b"], "delete_urls": ["ub"], "overflow": []},
        ], "summary": {"clusters": 1, "leads_deleted": 1}}
        pd = mock.MagicMock()
        with mock.patch.object(cli.push, "PipedriveClient") as PC, \
             mock.patch("sys.stderr", new_callable=io.StringIO), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            PC.return_value.__enter__.return_value = pd
            rc = cli._apply(plan, config.settings())
        self.assertEqual(rc, 0)
        pd.patch.assert_not_called()      # dry-run: no writes
        pd.delete.assert_not_called()     # dry-run: no deletes
        result = json.loads(out.getvalue())
        self.assertFalse(result["applied"])         # nothing actually applied
        self.assertEqual(result["leads_deleted"], 1)  # but reports would-delete count
        config.settings.cache_clear()
