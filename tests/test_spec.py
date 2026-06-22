"""Tests for pipeline/spec.py — the campaign spec schema + loader.

Phase 0: no behavior change. These pin the schema contract and confirm the
hand-written cleaning spec loads and validates. When Phase 1 wires the engine
to read a spec, the cleaning spec staying valid is the parity anchor.
"""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from pipeline.spec import (
    CAMPAIGNS_DIR,
    DEFAULT_CAMPAIGN_ID,
    CampaignSpec,
    load_campaign_spec,
    load_spec,
    resolve_spec_path,
)

CLEANING_SPEC = CAMPAIGNS_DIR / "aether-cleaning-az.yaml"


def _minimal_spec_dict() -> dict:
    """Smallest spec that should validate — used as a base to mutate per test."""
    return {
        "campaign_id": "test",
        "client": {
            "name": "Test Co",
            "company_description": "desc",
            "service_area": ["US"],
        },
        "targeting": {
            "industry": "widgets",
            "trigger_signals": ["funding"],
            "buyer_personas": ["CTO"],
        },
        "discovery": {"search_queries": ["widget funding"]},
        "qualification": {"relevance_rubric": "a funded widget co", "min_confidence": 0.6},
        "enrichment": {"buyer_persona": "CTO", "outreach_angle": "widgets"},
        "destination": {"type": "excel"},
    }


class TestCleaningSpec(unittest.TestCase):
    def test_default_campaign_resolves_to_cleaning_spec(self):
        self.assertEqual(
            resolve_spec_path(),
            CAMPAIGNS_DIR / f"{DEFAULT_CAMPAIGN_ID}.yaml",
        )
        self.assertEqual(load_campaign_spec().campaign_id, "aether-cleaning-az")

    def test_yaml_filename_resolves_under_campaigns_dir(self):
        self.assertEqual(
            resolve_spec_path("aether-cleaning-az.yaml"),
            CAMPAIGNS_DIR / "aether-cleaning-az.yaml",
        )

    def test_cleaning_spec_loads_and_validates(self):
        spec = load_spec(CLEANING_SPEC)
        self.assertIsInstance(spec, CampaignSpec)
        self.assertEqual(spec.campaign_id, "aether-cleaning-az")

    def test_cleaning_spec_captures_todays_hardcoded_values(self):
        """Guards against the spec drifting from what the code does today."""
        spec = load_spec(CLEANING_SPEC)
        # Discovery queries mirror sources.yaml google_news endpoints.
        self.assertIn("Arizona commercial real estate when:30d", spec.discovery.search_queries)
        self.assertEqual(spec.discovery.geography, ["AZ"])
        # CRE confidence gate.
        self.assertEqual(spec.qualification.min_confidence, 0.6)
        # Pipedrive destination with Excel fallback (plan §6).
        self.assertEqual(spec.destination.type, "pipedrive")
        self.assertEqual(spec.destination.fallback, "excel")
        self.assertIsNotNone(spec.destination.pipedrive)
        # Schedule guardrails.
        self.assertTrue(spec.schedule.preview_before_push)
        self.assertEqual(spec.schedule.max_first_push, 2)

    def test_outreach_angle_is_not_cleaning_voice(self):
        """Aether brand voice: asset preservation, never 'cleaning'/'janitor'."""
        spec = load_spec(CLEANING_SPEC)
        angle = spec.enrichment.outreach_angle.lower()
        self.assertIn("asset", angle)


class TestSchemaContract(unittest.TestCase):
    def test_minimal_spec_validates(self):
        spec = CampaignSpec.model_validate(_minimal_spec_dict())
        # Schedule defaults apply when omitted.
        self.assertEqual(spec.schedule.cadence, "nightly")
        self.assertTrue(spec.schedule.preview_before_push)

    def test_min_confidence_out_of_range_rejected(self):
        data = _minimal_spec_dict()
        data["qualification"]["min_confidence"] = 1.5
        with self.assertRaises(ValidationError):
            CampaignSpec.model_validate(data)

    def test_pipedrive_destination_requires_config_block(self):
        data = _minimal_spec_dict()
        data["destination"] = {"type": "pipedrive"}  # no pipedrive block
        with self.assertRaises(ValidationError):
            CampaignSpec.model_validate(data)

    def test_empty_campaign_id_rejected(self):
        data = _minimal_spec_dict()
        data["campaign_id"] = ""
        with self.assertRaises(ValidationError):
            CampaignSpec.model_validate(data)

    def test_unknown_destination_type_rejected(self):
        data = _minimal_spec_dict()
        data["destination"] = {"type": "salesforce"}
        with self.assertRaises(ValidationError):
            CampaignSpec.model_validate(data)


if __name__ == "__main__":
    unittest.main()
