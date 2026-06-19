"""Tests for pipeline.cli.merge_contacts."""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock


def _settings(dry):
    from pipeline import config
    config.settings.cache_clear()
    os.environ.update({
        "PIPEDRIVE_API_TOKEN": "t", "PIPEDRIVE_DOMAIN": "d",
        "PIPEDRIVE_FIELD_ARTICLE_URL": "URLHASH",
        "PIPEDRIVE_FIELD_LEAD_1": "L1", "PIPEDRIVE_FIELD_LEAD_2": "L2",
        "PIPEDRIVE_FIELD_LEAD_3": "L3", "DRY_RUN": "1" if dry else "0",
    })
    return config.settings()


class TestMergeContacts(unittest.TestCase):
    def tearDown(self):
        from pipeline import config
        config.settings.cache_clear()

    def test_merges_into_empty_slots_and_patches(self):
        _settings(dry=False)
        from pipeline.cli import merge_contacts as cli
        keeper = {"id": "k", "L1": "Jane | CEO", "L2": None, "L3": None}
        pd = mock.MagicMock()
        pd.get.return_value = keeper
        with mock.patch.object(cli.push, "PipedriveClient") as PC, \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "keeper_lead_id": "k",
                 "contacts": ["Bob | COO", "Jane | Chief Exec"],  # Jane dup by name
             }))):
            PC.return_value.__enter__.return_value = pd
            rc = cli.main()
        self.assertEqual(rc, 0)
        # PATCH called with L1 kept, L2 filled with Bob, Jane-dup dropped.
        patched = pd.patch.call_args.args[2]
        self.assertEqual(patched.get("L2"), "Bob | COO")
        self.assertNotIn("Jane | Chief Exec", patched.values())

    def test_dry_run_does_not_patch(self):
        _settings(dry=True)
        from pipeline.cli import merge_contacts as cli
        pd = mock.MagicMock()
        pd.get.return_value = {"id": "k", "L1": None, "L2": None, "L3": None}
        with mock.patch.object(cli.push, "PipedriveClient") as PC, \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "keeper_lead_id": "k", "contacts": ["Bob | COO"]}))):
            PC.return_value.__enter__.return_value = pd
            rc = cli.main()
        self.assertEqual(rc, 0)
        pd.patch.assert_not_called()
