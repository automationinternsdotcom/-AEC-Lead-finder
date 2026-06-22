"""Tests for Phase 2B pattern modules."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from pipeline.patterns import EntityAggregationPattern, EventSignalPattern, get_pattern_module
from pipeline.spec import load_campaign_spec_v2
from schema import ExtractedArticle

FIXTURE = Path(__file__).parent / "fixtures" / "entity_observations.json"


def _article(**overrides) -> dict:
    base = dict(
        title="Phoenix apartment tower reaches lease-up",
        published_date=None,
        summary_2sent="A Phoenix apartment tower entered lease-up after construction completion.",
        signal_type="construction",
        company_name="Metro Tower Partners",
        company_domain_guess=None,
        property_type="multifamily",
        address=None,
        city="Phoenix",
        square_footage=None,
        dollar_value=None,
        unit_count=240,
        az_relevant=True,
        confidence=0.88,
        priority="high",
        filter_reason="New multifamily lease-up in Aether's corridor.",
        service_angle="Lease-up phase signals need for asset-preservation partner.",
    )
    base.update(overrides)
    return ExtractedArticle.model_validate(base).model_dump(mode="json")


class TestEventSignalPattern(unittest.TestCase):
    def test_wraps_existing_qualification_rules(self):
        spec = load_campaign_spec_v2()
        result = EventSignalPattern().run(
            [
                _article(),
                _article(
                    title="National mortgage rates tick higher",
                    priority="low",
                    confidence=0.9,
                    service_angle=None,
                    filter_reason="Mortgage-rate commentary with no property activity.",
                ),
            ],
            spec,
        )
        self.assertEqual(result.pattern_type, "event_signal")
        self.assertEqual(result.stats["total"], 2)
        self.assertEqual(result.stats["qualified"], 1)
        self.assertTrue(result.records[0].qualified)
        self.assertFalse(result.records[1].qualified)
        self.assertEqual(result.records[1].filter_reason, "low_priority")


class TestEntityAggregationPattern(unittest.TestCase):
    def test_fixture_backed_entity_aggregation(self):
        spec = load_campaign_spec_v2()
        records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        result = EntityAggregationPattern().run(records, spec)

        self.assertEqual(result.pattern_type, "entity_aggregation")
        self.assertEqual(result.stats["total"], 3)
        self.assertEqual(result.stats["needs_codex_adjudication"], 2)
        clean = [r for r in result.records if r.entity_name == "Desert Ridge Property Management LLC"][0]
        self.assertTrue(clean.qualified)
        self.assertFalse(clean.needs_codex_adjudication)
        ambiguous = [r for r in result.records if r.entity_name == "Sun Mesa Holdings LLC"][0]
        self.assertFalse(ambiguous.qualified)
        self.assertEqual(ambiguous.filter_reason, "needs_codex_adjudication")


class TestPatternRegistry(unittest.TestCase):
    def test_returns_implemented_modules(self):
        self.assertIsInstance(get_pattern_module("event_signal"), EventSignalPattern)
        self.assertIsInstance(get_pattern_module("entity_aggregation"), EntityAggregationPattern)

    def test_future_patterns_are_contract_only(self):
        with self.assertRaises(NotImplementedError):
            get_pattern_module("multi_signal_intent")


if __name__ == "__main__":
    unittest.main()
