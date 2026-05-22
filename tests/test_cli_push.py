"""Tests for pipeline.cli.push — reads JSON stdin, prints lead_id JSON."""

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
            "priority": "high",
            "filter_reason": "Tempe retail lease — active occupancy",
            "service_angle": "Asset preservation partner for new tenant fit-out",
        },
        "lead": None,
        "url": "https://example.com/a",
    }
    base.update(overrides)
    return json.dumps(base)


class TestPushCli(unittest.TestCase):
    def test_prints_lead_id_on_create(self):
        with patch("pipeline.cli.push.config.settings", return_value=None), \
             patch("pipeline.cli.push.config.load_rates", return_value={}), \
             patch("pipeline.cli.push.push.sync_to_pipedrive",
                   return_value=(7, None, "new-lead-uuid")), \
             patch("sys.stdin", io.StringIO(_input_doc())), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = push_cli.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["lead_id"], "new-lead-uuid")
        self.assertEqual(data["org_id"], 7)
        self.assertFalse(data["skipped"])

    def test_skipped_when_existing_lead(self):
        """Pipedrive dedup hit — sync_to_pipedrive returns (None, None, existing_uuid)."""
        with patch("pipeline.cli.push.config.settings", return_value=None), \
             patch("pipeline.cli.push.config.load_rates", return_value={}), \
             patch("pipeline.cli.push.push.sync_to_pipedrive",
                   return_value=(None, None, "existing-lead-uuid")), \
             patch("sys.stdin", io.StringIO(_input_doc())), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            push_cli.main()
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["lead_id"], "existing-lead-uuid")
        self.assertTrue(data["skipped"])


if __name__ == "__main__":
    unittest.main()
