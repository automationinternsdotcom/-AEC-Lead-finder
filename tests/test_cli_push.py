"""Tests for pipeline.cli.push — reads JSON stdin, prints deal_id JSON."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pipeline.cli import push as push_cli


def _input_doc(**overrides):
    base = {
        "article": {
            "title": "Tempe retail tower", "published_date": None,
            "summary_2sent": "Tempe retail tower lease.",
            "signal_type": "lease", "company_name": "Acme",
            "company_domain_guess": "acme.com", "property_type": "retail",
            "address": None, "city": "Tempe", "square_footage": 10000,
            "dollar_value": None, "unit_count": None,
            "az_relevant": True, "confidence": 0.8,
        },
        "lead": None,
        "url": "https://example.com/a",
    }
    base.update(overrides)
    return json.dumps(base)


class TestPushCli(unittest.TestCase):
    def test_prints_deal_id_on_create(self):
        with patch("pipeline.cli.push.push.sync_to_pipedrive",
                   return_value=(7, None, 555)), \
             patch("sys.stdin", io.StringIO(_input_doc())), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = push_cli.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["deal_id"], 555)
        self.assertEqual(data["org_id"], 7)
        self.assertFalse(data["skipped"])

    def test_skipped_when_existing_deal(self):
        """Pipedrive dedup hit — sync_to_pipedrive returns (None, None, existing_id)."""
        with patch("pipeline.cli.push.push.sync_to_pipedrive",
                   return_value=(None, None, 999)), \
             patch("sys.stdin", io.StringIO(_input_doc())), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            push_cli.main()
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["deal_id"], 999)
        self.assertTrue(data["skipped"])


if __name__ == "__main__":
    unittest.main()
