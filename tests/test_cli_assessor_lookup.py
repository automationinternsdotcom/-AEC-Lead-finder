"""Tests for pipeline.cli.assessor_lookup — stdout JSON or null."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pipeline.cli import assessor_lookup as cli


class TestAssessorLookupCli(unittest.TestCase):
    def test_prints_json_on_hit(self):
        with patch("pipeline.cli.assessor_lookup.assessor.lookup_by_address",
                   return_value={"owner": "ABC Holdings LLC",
                                 "mailing_address": "100 Main St, Phoenix, 85003",
                                 "apn": "123-45-678",
                                 "property_type": "COMMERCIAL"}), \
             patch("sys.argv", ["prog", "100 Main St, Phoenix AZ"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cli.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["owner"], "ABC Holdings LLC")
        self.assertEqual(data["apn"], "123-45-678")

    def test_prints_null_on_miss(self):
        with patch("pipeline.cli.assessor_lookup.assessor.lookup_by_address",
                   return_value=None), \
             patch("sys.argv", ["prog", "100 nowhere, Tucson"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            cli.main()
        self.assertEqual(stdout.getvalue().strip(), "null")

    def test_exits_2_on_missing_arg(self):
        with patch("sys.argv", ["prog"]), patch("sys.stderr"):
            rc = cli.main()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
