"""Tests for deterministic Phase 2 scoring."""
from __future__ import annotations

import unittest

from pipeline.scoring import score_entity_aggregate, score_event_signal, sort_by_score_desc
from pipeline.spec import load_campaign_spec_v2
from schema import ExtractedArticle


def _article(**overrides) -> ExtractedArticle:
    base = dict(
        title="Tempe retail center signs new restaurant tenant",
        published_date=None,
        summary_2sent="A new restaurant signed a lease in a Tempe retail center.",
        signal_type="lease",
        company_name="Acme Retail",
        company_domain_guess=None,
        property_type="retail",
        address=None,
        city="Tempe",
        square_footage=None,
        dollar_value=None,
        unit_count=None,
        az_relevant=True,
        confidence=0.82,
        priority="high",
        filter_reason="New tenant occupancy at an Arizona commercial property.",
        service_angle="Asset-preservation partner timing is strong.",
    )
    base.update(overrides)
    return ExtractedArticle.model_validate(base)


class TestEventSignalScoring(unittest.TestCase):
    def test_high_quality_event_scores_above_low_priority(self):
        spec = load_campaign_spec_v2()
        high = score_event_signal(_article(), spec)
        low = score_event_signal(
            _article(priority="low", confidence=0.9, service_angle=None),
            spec,
        )
        self.assertGreater(high.total, low.total)
        self.assertIn("matches event-signal scoring criteria", high.reasons)

    def test_out_of_geo_penalty_applies(self):
        spec = load_campaign_spec_v2()
        score = score_event_signal(_article(az_relevant=False), spec)
        self.assertIn("property outside campaign geography", score.reasons)
        self.assertLess(score.total, 60)


class TestEntityAggregateScoring(unittest.TestCase):
    def test_entity_score_rewards_multiple_observations(self):
        spec = load_campaign_spec_v2()
        single = score_entity_aggregate(
            confidences=[0.9],
            signal_count=1,
            observation_count=1,
            needs_adjudication=False,
            spec=spec,
        )
        multi = score_entity_aggregate(
            confidences=[0.9, 0.85, 0.8],
            signal_count=2,
            observation_count=3,
            needs_adjudication=False,
            spec=spec,
        )
        self.assertGreater(multi.total, single.total)

    def test_adjudication_penalty_is_visible(self):
        spec = load_campaign_spec_v2()
        score = score_entity_aggregate(
            confidences=[0.9, 0.9],
            signal_count=1,
            observation_count=2,
            needs_adjudication=True,
            spec=spec,
        )
        self.assertIn("requires Codex adjudication before enrichment", score.reasons)
        self.assertGreater(score.penalty_points, 0)

    def test_sort_by_score_desc(self):
        records = [{"id": "b", "score": 10}, {"id": "a", "score": 90}]
        self.assertEqual([r["id"] for r in sort_by_score_desc(records)], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
