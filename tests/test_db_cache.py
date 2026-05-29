"""Tests for the enriched_orgs cache in pipeline/db.py."""

from __future__ import annotations

import sqlite3
import unittest

from pipeline import db
from pipeline.enrich import Lead


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


def _sample_lead() -> Lead:
    return Lead(
        name="Michael Wilson", title="COO, Mark-Taylor Inc",
        email="michael.wilson@mark-taylor.com", phone=None,
        linkedin_url="https://linkedin.com/in/michael-wilson",
        seniority="c_suite", apollo_id="grok",
    )


class TestNormalizeOrgName(unittest.TestCase):
    def test_strips_llc_suffix(self):
        self.assertEqual(db._normalize_org_name("Mark-Taylor Residential LLC"),
                         "mark taylor residential")

    def test_strips_inc_suffix(self):
        self.assertEqual(db._normalize_org_name("Acme, Inc."), "acme")

    def test_collapses_whitespace(self):
        self.assertEqual(db._normalize_org_name("  Mark-Taylor   Residential  "),
                         "mark taylor residential")

    def test_strips_punctuation(self):
        self.assertEqual(db._normalize_org_name("M&T Group, L.L.C."), "m t group")

    def test_idempotent(self):
        once = db._normalize_org_name("Mark-Taylor Residential LLC")
        twice = db._normalize_org_name(once)
        self.assertEqual(once, twice)

    def test_handles_common_suffixes(self):
        for suf in ("Corp", "Corporation", "Ltd", "LP", "LLP", "Inc"):
            self.assertEqual(db._normalize_org_name(f"Acme {suf}"), "acme")


class TestCacheRoundTrip(unittest.TestCase):
    def test_miss_returns_none(self):
        conn = _mem_conn()
        self.assertIsNone(db.get_cached_enrichment(conn, "Mark-Taylor"))

    def test_hit_returns_lead(self):
        conn = _mem_conn()
        lead = _sample_lead()
        db.cache_enrichment(conn, "Mark-Taylor Residential", lead, source="grok")
        conn.commit()
        cached = db.get_cached_enrichment(conn, "Mark-Taylor Residential")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.name, "Michael Wilson")
        self.assertEqual(cached.apollo_id, "grok")
        self.assertEqual(cached.email, "michael.wilson@mark-taylor.com")

    def test_hit_via_normalized_name_variation(self):
        """Cached as 'Mark-Taylor Residential LLC'; queried as 'mark taylor residential'."""
        conn = _mem_conn()
        db.cache_enrichment(conn, "Mark-Taylor Residential LLC", _sample_lead(), source="grok")
        conn.commit()
        cached = db.get_cached_enrichment(conn, "mark taylor residential")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.name, "Michael Wilson")

    def test_overwrite_replaces(self):
        """Second cache for same normalized name overwrites — fresher data wins."""
        conn = _mem_conn()
        lead_v1 = _sample_lead()
        db.cache_enrichment(conn, "Mark-Taylor", lead_v1, source="grok")
        lead_v2 = Lead(name="Different Person", title="x", email=None, phone=None,
                       linkedin_url=None, seniority="", apollo_id="grok")
        db.cache_enrichment(conn, "Mark-Taylor", lead_v2, source="grok")
        conn.commit()
        cached = db.get_cached_enrichment(conn, "Mark-Taylor")
        self.assertEqual(cached.name, "Different Person")


if __name__ == "__main__":
    unittest.main()
