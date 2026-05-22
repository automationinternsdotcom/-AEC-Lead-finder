"""Tests for pipeline.cli.qualify — exit 0 if pass, 1 if drop."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pipeline.cli import qualify as qualify_cli


def _article_json(**overrides) -> str:
    base = {
        "title": "x", "published_date": None, "summary_2sent": "x",
        "signal_type": "lease", "company_name": "Acme",
        "company_domain_guess": None, "property_type": "retail",
        "address": None, "city": "Tempe", "square_footage": None,
        "dollar_value": None, "unit_count": None,
        "az_relevant": True, "confidence": 0.7,
    }
    base.update(overrides)
    return json.dumps(base)


class TestQualifyCli(unittest.TestCase):
    def test_exit_0_when_qualifying(self):
        with patch("sys.stdin", io.StringIO(_article_json())):
            rc = qualify_cli.main()
        self.assertEqual(rc, 0)

    def test_exit_1_with_reason_when_not_az(self):
        with patch("sys.stdin", io.StringIO(_article_json(az_relevant=False))), \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            rc = qualify_cli.main()
        self.assertEqual(rc, 1)
        self.assertIn("not_az", stderr.getvalue())

    def test_exit_1_with_reason_when_low_confidence(self):
        with patch("sys.stdin", io.StringIO(_article_json(confidence=0.3))), \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            rc = qualify_cli.main()
        self.assertEqual(rc, 1)
        self.assertIn("low_conf", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
