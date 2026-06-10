"""Tests for two-month historical lead discovery."""

from __future__ import annotations

import unittest


class TestBackfillSources(unittest.TestCase):
    def test_build_sources_uses_requested_day_window_and_expanded_queries(self):
        from pipeline import backfill

        sources = backfill.build_backfill_sources(days=60)

        self.assertGreaterEqual(len(sources), 8)
        self.assertTrue(all(src["method"] == "google_news" for src in sources))
        self.assertTrue(all(src["enabled"] for src in sources))
        self.assertTrue(all("when:60d" in src["endpoint"] for src in sources))
        query_text = " ".join(src["endpoint"] for src in sources).lower()
        self.assertIn("multifamily", query_text)
        self.assertIn("property management", query_text)
        self.assertIn("lease", query_text)


if __name__ == "__main__":
    unittest.main()
