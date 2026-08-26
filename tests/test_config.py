"""Tests for pipeline.config source loading."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline import config


class TestNewsWebsiteSources(unittest.TestCase):
    def test_load_news_websites_csv_as_enabled_website_sources(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "news_websites.csv"
            path.write_text(
                "Resource Name,URL\n"
                "AZ Big Media – Commercial RE,https://azbigmedia.com/category/real-estate/\n"
                "AZ Big Media – Commercial RE,https://azbigmedia.com/real-estate\n",
                encoding="utf-8",
            )

            sources = config.load_news_websites_csv(path)

        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["name"], "az_big_media_commercial_re")
        self.assertEqual(sources[1]["name"], "az_big_media_commercial_re_2")
        self.assertEqual(sources[0]["method"], "website")
        self.assertTrue(sources[0]["enabled"])


if __name__ == "__main__":
    unittest.main()
