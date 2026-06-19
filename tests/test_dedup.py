"""Tests for pipeline/dedup.py and its config knobs."""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone


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


class TestScoringAndClustering(unittest.TestCase):
    # Real near-duplicate headlines (same event, syndicated across feeds).
    CRG_A = "CRG Sells Industrial Building at 1.2 MSF Cubes at Mesa Gateway"
    CRG_B = "CRG Sells 1.2M SF Industrial Building at The Cubes at Mesa Gateway"
    UNRELATED = "Creation buys 38-acre site to build Avondale Tech Center"

    def test_same_event_scores_high(self):
        from pipeline import dedup
        self.assertGreaterEqual(dedup.same_event_score(self.CRG_A, self.CRG_B), 0.5)

    def test_unrelated_scores_low(self):
        from pipeline import dedup
        self.assertLess(dedup.same_event_score(self.CRG_A, self.UNRELATED), 0.5)

    def test_cluster_groups_same_event(self):
        from pipeline import dedup
        recs = [
            dedup.LeadRecord("1", self.CRG_A, None, [], None, 0),
            dedup.LeadRecord("2", self.CRG_B, None, [], None, 0),
            dedup.LeadRecord("3", self.UNRELATED, None, [], None, 0),
        ]
        clusters = dedup.cluster_leads(recs, threshold=0.5)
        sizes = sorted(len(c) for c in clusters)
        self.assertEqual(sizes, [1, 2])


class TestKeeperAndMerge(unittest.TestCase):
    def _rec(self, lid, contacts, num_filled, day):
        from pipeline import dedup
        return dedup.LeadRecord(
            lid, "t", "u", contacts,
            datetime(2026, 5, day, tzinfo=timezone.utc), num_filled,
        )

    def test_keeper_prefers_most_contacts_then_fields_then_earliest(self):
        from pipeline import dedup
        a = self._rec("a", ["X | CEO"], num_filled=3, day=30)
        b = self._rec("b", ["X | CEO", "Y | COO"], num_filled=2, day=31)  # more contacts
        c = self._rec("c", ["X | CEO", "Y | COO"], num_filled=2, day=29)  # tie -> earliest
        keeper = max([a, b, c], key=dedup.completeness_key)
        self.assertEqual(keeper.lead_id, "c")

    def test_merge_dedups_by_name_keeps_keeper_first_caps_at_3(self):
        from pipeline import dedup
        res = dedup.merge_contact_strings(
            existing=["Jane Doe | CEO | jane@x.com"],
            incoming=[
                "Jane Doe | Chief Executive",          # same person, diff text -> dropped
                "Bob Smith | COO | bob@x.com",
                "Cara Lee | VP",
                "Dan Poe | Director",                  # 4th unique -> overflow
            ],
        )
        self.assertEqual(res.kept[0], "Jane Doe | CEO | jane@x.com")  # keeper first
        self.assertEqual(len(res.kept), 3)
        self.assertEqual([c.split(" | ")[0] for c in res.kept],
                         ["Jane Doe", "Bob Smith", "Cara Lee"])
        self.assertEqual([c.split(" | ")[0] for c in res.overflow], ["Dan Poe"])

    def test_keeper_none_date_loses_tiebreak(self):
        from pipeline import dedup
        dated = self._rec("dated", ["X | CEO"], num_filled=1, day=30)
        undated = dedup.LeadRecord("undated", "t", "u", ["X | CEO"], None, 1)
        keeper = max([undated, dated], key=dedup.completeness_key)
        self.assertEqual(keeper.lead_id, "dated")  # real date beats missing date
