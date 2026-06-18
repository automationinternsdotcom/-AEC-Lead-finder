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


class TestNormalization(unittest.TestCase):
    def test_title_tokens_drops_digits_units_stopwords(self):
        from pipeline import dedup
        toks = dedup.title_tokens(
            "SkySong leasing activity tops 28,000 square feet as ASU Innovation Center"
        )
        self.assertIn("skysong", toks)
        self.assertIn("leasing", toks)
        self.assertIn("innovation", toks)
        self.assertNotIn("28", toks)          # digits dropped
        self.assertNotIn("square", toks)      # unit word dropped
        self.assertNotIn("feet", toks)
        self.assertNotIn("as", toks)          # stopword + too short

    def test_normalize_company_strips_suffix_and_parens(self):
        from pipeline import dedup
        self.assertEqual(
            dedup.normalize_company("Plaza Companies (SkySong)"), "plaza"
        )
        self.assertEqual(dedup.normalize_company("Foundation 8 LLC"), "foundation 8")
        self.assertEqual(
            dedup.normalize_company("Stevens-Leinweber Construction"),
            "stevens-leinweber",
        )

    def test_normalize_company_keeps_one_token_for_all_suffix_names(self):
        from pipeline import dedup
        # An all-suffix name must NOT collapse to "" (would false-match).
        self.assertEqual(dedup.normalize_company("Capital Group"), "capital")
        self.assertEqual(dedup.normalize_company("Development LLC"), "development")

    def test_normalize_company_handles_none(self):
        from pipeline import dedup
        self.assertEqual(dedup.normalize_company(None), "")
