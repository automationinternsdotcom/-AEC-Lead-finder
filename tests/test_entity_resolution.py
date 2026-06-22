"""Tests for deterministic entity aggregation helpers."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from pipeline.entity_resolution import (
    EntityObservation,
    group_entity_observations,
    normalize_entity_name,
)

FIXTURE = Path(__file__).parent / "fixtures" / "entity_observations.json"


class TestNormalizeEntityName(unittest.TestCase):
    def test_strips_suffixes_and_punctuation(self):
        self.assertEqual(
            normalize_entity_name("Desert Ridge Property Management, L.L.C."),
            "desert ridge property management",
        )

    def test_generic_names_collapse_to_empty(self):
        self.assertEqual(normalize_entity_name("Property Manager"), "")

    def test_generic_observation_is_rejected(self):
        with self.assertRaises(ValidationError):
            EntityObservation(entity_name="Property Manager", source="x")


class TestGroupEntityObservations(unittest.TestCase):
    def test_groups_fixture_records_and_flags_ambiguity(self):
        records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        groups = group_entity_observations(records)
        by_name = {g.canonical_name: g for g in groups}

        desert = by_name["Desert Ridge Property Management LLC"]
        self.assertEqual(len(desert.observations), 2)
        self.assertFalse(desert.needs_codex_adjudication)
        self.assertEqual(desert.domains, ["desertridgepm.com"])

        sun = by_name["Sun Mesa Holdings LLC"]
        self.assertTrue(sun.needs_codex_adjudication)
        self.assertIn("conflicting domains", sun.adjudication_reason)

        cactus = by_name["Cactus Village Apartments"]
        self.assertTrue(cactus.needs_codex_adjudication)
        self.assertIn("single low-confidence observation", cactus.adjudication_reason)


if __name__ == "__main__":
    unittest.main()
