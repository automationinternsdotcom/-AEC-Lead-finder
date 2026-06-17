"""Tests for pipeline/dedup.py and its config knobs."""
from __future__ import annotations

import os
import unittest


class TestDedupConfig(unittest.TestCase):
    def test_defaults(self):
        # Re-import with a clean lru_cache so env defaults apply.
        from pipeline import config
        config.settings.cache_clear()
        for k in ("DEDUP_WINDOW_DAYS", "DEDUP_SCORE_THRESHOLD"):
            os.environ.pop(k, None)
        os.environ.setdefault("PIPEDRIVE_API_TOKEN", "t")
        os.environ.setdefault("PIPEDRIVE_DOMAIN", "d")
        os.environ.setdefault("PIPEDRIVE_FIELD_ARTICLE_URL", "f")
        s = config.settings()
        self.assertEqual(s.dedup_window_days, 14)
        self.assertAlmostEqual(s.dedup_score_threshold, 0.5)
        config.settings.cache_clear()
