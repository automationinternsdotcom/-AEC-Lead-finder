"""Entity-aggregation MVP pattern.

This groups deterministic observations into target entities and marks ambiguous
groups for Codex adjudication. It does not enrich contacts or deliver leads.
"""
from __future__ import annotations

from typing import Any

from pipeline.entity_resolution import EntityObservation, group_entity_observations
from pipeline.patterns.base import PatternCandidate, PatternResult
from pipeline.scoring import score_entity_aggregate
from pipeline.spec import CampaignSpecV2


class EntityAggregationPattern:
    pattern_type = "entity_aggregation"

    def run(self, records: list[dict[str, Any]], spec: CampaignSpecV2) -> PatternResult:
        observations = [EntityObservation.model_validate(record) for record in records]
        groups = group_entity_observations(observations)
        candidates: list[PatternCandidate] = []
        for group in groups:
            score = score_entity_aggregate(
                confidences=[obs.confidence for obs in group.observations],
                signal_count=len(group.signals),
                observation_count=len(group.observations),
                needs_adjudication=group.needs_codex_adjudication,
                spec=spec,
            )
            candidates.append(
                PatternCandidate(
                    candidate_id=group.entity_key,
                    pattern_type="entity_aggregation",
                    entity_name=group.canonical_name,
                    score=score.total,
                    qualified=not group.needs_codex_adjudication,
                    filter_reason=(
                        "needs_codex_adjudication"
                        if group.needs_codex_adjudication
                        else None
                    ),
                    needs_codex_adjudication=group.needs_codex_adjudication,
                    adjudication_reason=group.adjudication_reason,
                    evidence={
                        "score": score.model_dump(mode="json"),
                        "aliases": group.aliases,
                        "domains": group.domains,
                        "cities": group.cities,
                        "signals": group.signals,
                        "observation_count": len(group.observations),
                    },
                    raw=group.model_dump(mode="json"),
                )
            )
        return PatternResult.from_records("entity_aggregation", candidates)
