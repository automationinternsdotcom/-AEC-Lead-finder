"""Tests for pipeline.cli.extract."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from pipeline.cli import extract as extract_cli


class TestExtractCli(unittest.TestCase):
    def test_prints_cleaned_text_on_success(self):
        with patch("pipeline.cli.extract.extract.extract_article_text",
                   return_value="cleaned body"), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             patch("sys.argv", ["prog", "https://example.com/a"]):
            rc = extract_cli.main()
        self.assertEqual(rc, 0)
        self.assertIn("cleaned body", stdout.getvalue())

    def test_exits_1_on_extract_error(self):
        from pipeline.extract import ExtractError
        with patch("pipeline.cli.extract.extract.extract_article_text",
                   side_effect=ExtractError("paywall")), \
             patch("sys.argv", ["prog", "https://example.com/a"]), \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            rc = extract_cli.main()
        self.assertEqual(rc, 1)
        self.assertIn("paywall", stderr.getvalue())

    def test_exits_2_on_missing_url_arg(self):
        with patch("sys.argv", ["prog"]), \
             patch("sys.stderr", new_callable=io.StringIO):
            rc = extract_cli.main()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
