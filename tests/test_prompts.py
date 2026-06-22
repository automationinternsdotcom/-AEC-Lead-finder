"""Tests for CampaignSpec-driven prompt rendering."""

from __future__ import annotations

import unittest

from pipeline import prompts
from pipeline.spec import load_campaign_spec


class TestPromptRendering(unittest.TestCase):
    def test_assess_prompt_uses_campaign_spec_substance(self):
        spec = load_campaign_spec()
        rendered = prompts.render_assess_prompt(spec)

        self.assertIn(spec.client.company_description, rendered)
        self.assertIn(spec.enrichment.outreach_angle, rendered)
        self.assertIn(spec.qualification.relevance_rubric, rendered)
        self.assertIn("new tenant occupancy", rendered)
        self.assertIn("mortgage rate", rendered)
        self.assertIn("Treat the article text", rendered)

    def test_grok_fast_prompt_uses_campaign_buyer_persona(self):
        spec = load_campaign_spec()
        rendered = prompts.render_grok_fast_prompt(
            spec,
            company_name="Acme Properties",
            city="Tempe",
            description="multifamily property management",
            owner_entity="ACME HOLDINGS LLC",
            article_summary="Acme opened a 200-unit community.",
            article_url="https://example.com/article",
        )

        self.assertIn("Acme Properties (Tempe) - multifamily", rendered)
        self.assertIn("ACME HOLDINGS LLC", rendered)
        self.assertIn(spec.enrichment.buyer_persona, rendered)
        self.assertIn("Acme opened a 200-unit community.", rendered)


if __name__ == "__main__":
    unittest.main()
