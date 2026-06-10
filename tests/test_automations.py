"""Tests for safe Pipedrive follow-up automation payloads."""

from __future__ import annotations

from datetime import date
import unittest

from pipeline.enrich import Lead
from schema import ExtractedArticle


def _article(**overrides) -> ExtractedArticle:
    base = {
        "title": "Consumer Cellular signs Valley's largest office lease",
        "published_date": "2026-06-04",
        "summary_2sent": "Consumer Cellular signed a large Phoenix office lease.",
        "signal_type": "lease",
        "company_name": "Consumer Cellular",
        "company_domain_guess": "consumercellular.com",
        "property_type": "office",
        "address": "1 N Central Ave, Phoenix, AZ",
        "city": "Phoenix",
        "square_footage": 50000,
        "dollar_value": None,
        "unit_count": None,
        "az_relevant": True,
        "confidence": 0.9,
        "priority": "high",
        "filter_reason": "Large new office lease creates move-in service need.",
        "service_angle": "Reach out before move-in to position Aether as an asset-preservation partner.",
    }
    base.update(overrides)
    return ExtractedArticle.model_validate(base)


class TestBuildFollowupActivities(unittest.TestCase):
    def test_builds_call_and_email_reminder_payloads(self):
        from pipeline import automations

        lead = Lead(
            name="Jane Doe",
            title="COO",
            email="jane@consumer.example",
            phone="480-555-1212",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            seniority="c_suite",
            apollo_id="grok",
            email_verified=True,
            phone_verified=True,
        )

        payloads = automations.build_followup_activities(
            lead_id="abc-lead-uuid",
            org_id=10,
            person_id=20,
            article=_article(),
            primary=lead,
            url="https://example.com/article",
            owner_id=123,
            today=date(2026, 6, 4),
        )

        self.assertEqual([p["type"] for p in payloads], ["call", "email"])
        self.assertEqual(payloads[0]["due_date"], "2026-06-05")
        self.assertEqual(payloads[0]["lead_id"], "abc-lead-uuid")
        self.assertEqual(payloads[0]["person_id"], 20)
        self.assertEqual(payloads[0]["org_id"], 10)
        self.assertEqual(payloads[0]["owner_id"], 123)
        self.assertIn("Jane Doe", payloads[0]["note"])
        self.assertIn("asset-preservation partner", payloads[0]["note"])
        self.assertIn("https://example.com/article", payloads[0]["note"])
        self.assertIn("Draft email", payloads[1]["note"])
        self.assertIn("https://www.linkedin.com/in/jane-doe", payloads[1]["note"])

    def test_next_business_day_skips_weekend(self):
        from pipeline import automations

        self.assertEqual(
            automations.next_business_day(date(2026, 6, 5)).isoformat(),
            "2026-06-08",
        )


if __name__ == "__main__":
    unittest.main()
