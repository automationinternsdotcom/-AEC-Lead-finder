"""Tests for persona-aware enrichment cache."""
from __future__ import annotations

import sqlite3
import unittest

from pipeline.cache import (
    build_persona_cache_key,
    cache_leads,
    get_cached_leads,
    normalize_org_name,
    normalize_persona,
)
from pipeline.enrich import Lead


def _lead(name: str = "Jordan Smith") -> Lead:
    return Lead(
        name=name,
        title="Owner",
        email="jordan@example.com",
        phone=None,
        linkedin_url=None,
        seniority="owner",
        apollo_id="grok",
    )


class TestPersonaAwareCache(unittest.TestCase):
    def test_key_includes_campaign_org_and_persona(self):
        key = build_persona_cache_key(
            "campaign",
            "Acme Property Management LLC",
            "Asset Manager / Owner",
        )
        self.assertEqual(key.org_normalized, "acme property management")
        self.assertEqual(key.persona_key, "asset manager owner")
        self.assertEqual(key.value, "campaign:acme property management:asset manager owner")

    def test_persona_isolated_cache_hits(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cache_leads(
            conn,
            campaign_id="campaign",
            org_name="Acme LLC",
            persona="Owner",
            leads=[_lead("Owner Person")],
            source="grok",
        )
        conn.commit()
        self.assertEqual(
            get_cached_leads(conn, campaign_id="campaign", org_name="Acme", persona="Owner")[0].name,
            "Owner Person",
        )
        self.assertEqual(
            get_cached_leads(conn, campaign_id="campaign", org_name="Acme", persona="Facilities"),
            [],
        )

    def test_normalizers_are_stable(self):
        self.assertEqual(normalize_org_name("M&T Group, L.L.C."), "m t group")
        self.assertEqual(normalize_persona("VP/Director of Facilities"), "vp director of facilities")


if __name__ == "__main__":
    unittest.main()
