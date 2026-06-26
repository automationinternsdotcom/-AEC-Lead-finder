"""Tests for the Phase 2 resolved CampaignSpec contract."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml
from pydantic import ValidationError

from pipeline.spec import (
    CAMPAIGNS_DIR,
    CampaignSpecV2,
    DestinationV2,
    LeadPattern,
    campaign_spec_v1_to_v2,
    load_campaign_spec,
    load_campaign_spec_v2,
)


class TestCampaignSpecV2Compatibility(unittest.TestCase):
    def test_default_phase1_spec_resolves_to_v2_event_signal(self):
        spec = load_campaign_spec_v2()
        self.assertEqual(spec.schema_version, "campaign_spec.v2")
        self.assertEqual(spec.campaign_id, "aether-cleaning-az")
        self.assertEqual(spec.lead_pattern.type, "event_signal")
        self.assertEqual(spec.routing.discover, "deterministic_cli")
        self.assertEqual(spec.routing.fetch, "deterministic_cli")
        self.assertEqual(spec.routing.pattern, "deterministic_cli")
        self.assertEqual(spec.routing.enrich, "browser_chat_skill")
        self.assertTrue(spec.quality_gates.preserve_phase1_parity)
        self.assertIn("google_news_az_cre", spec.sources.source_tags)
        self.assertEqual(spec.sources.provider, "gemini_api")
        self.assertEqual(spec.sources.max_sources, 100)
        self.assertEqual(spec.sources.gemini.model, "gemini-3.1-pro-preview")
        self.assertEqual(spec.sources.dedupe.namespace, "aether-cleaning-az")

    def test_phase1_loader_still_returns_phase1_model(self):
        phase1 = load_campaign_spec()
        phase2 = campaign_spec_v1_to_v2(phase1)
        self.assertTrue(hasattr(phase1, "client"))
        self.assertEqual(phase2.identity.client_name, phase1.client.name)
        self.assertEqual(phase2.qualification.min_confidence, phase1.qualification.min_confidence)

    def test_explicit_v2_yaml_loads(self):
        data = {
            "schema_version": "campaign_spec.v2",
            "identity": {
                "campaign_id": "test-v2",
                "name": "Test V2",
                "client_name": "Client",
            },
            "target_profile": {"industry": "property management"},
            "lead_pattern": {"type": "entity_aggregation"},
            "signals": {"trigger_signals": ["portfolio growth"]},
            "sources": {"search_queries": ["apartments phoenix"]},
            "qualification": {"relevance_rubric": "Find target entities.", "min_confidence": 0.5},
            "enrichment": {"buyer_persona": "Owner", "outreach_angle": "portfolio operations"},
            "destinations": [{"type": "excel"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "campaign.yaml"
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            spec = load_campaign_spec_v2(path)
        self.assertEqual(spec.campaign_id, "test-v2")
        self.assertEqual(spec.lead_pattern.type, "entity_aggregation")
        self.assertEqual(spec.destinations[0].type, "excel")

    def test_phase1_template_upconverts_to_v2(self):
        spec = load_campaign_spec_v2(CAMPAIGNS_DIR / "_template.yaml")
        self.assertEqual(spec.schema_version, "campaign_spec.v2")
        self.assertEqual(spec.campaign_id, "example-campaign")
        self.assertEqual(spec.destinations[0].type, "excel")
        self.assertEqual(spec.run_policy.cadence, "manual")


class TestCampaignSpecV2Validation(unittest.TestCase):
    def test_live_destination_requires_credential_ref(self):
        with self.assertRaises(ValidationError):
            DestinationV2(type="pipedrive")

    def test_future_pattern_names_are_contract_supported(self):
        pattern = LeadPattern(type="multi_signal_intent")
        self.assertEqual(pattern.type, "multi_signal_intent")

    def test_unknown_pattern_rejected(self):
        with self.assertRaises(ValidationError):
            LeadPattern(type="made_up")

    def test_min_confidence_range_still_validates(self):
        data = load_campaign_spec_v2().model_dump(mode="json")
        data["qualification"]["min_confidence"] = 1.2
        with self.assertRaises(ValidationError):
            CampaignSpecV2.model_validate(data)


if __name__ == "__main__":
    unittest.main()
