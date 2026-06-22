"""Small pattern-module contract for Phase 2B."""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from pipeline.spec import CampaignSpecV2, LeadPatternType


class PatternCandidate(BaseModel):
    candidate_id: str
    pattern_type: LeadPatternType
    entity_name: str
    score: int = Field(ge=0, le=100)
    qualified: bool = True
    filter_reason: str | None = None
    needs_codex_adjudication: bool = False
    adjudication_reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class PatternResult(BaseModel):
    pattern_type: LeadPatternType
    records: list[PatternCandidate] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def from_records(
        cls,
        pattern_type: LeadPatternType,
        records: list[PatternCandidate],
    ) -> "PatternResult":
        return cls(
            pattern_type=pattern_type,
            records=records,
            stats={
                "total": len(records),
                "qualified": sum(1 for r in records if r.qualified),
                "needs_codex_adjudication": sum(1 for r in records if r.needs_codex_adjudication),
            },
        )


class PatternModule(Protocol):
    pattern_type: LeadPatternType

    def run(self, records: list[dict[str, Any]], spec: CampaignSpecV2) -> PatternResult:
        """Convert raw stage records into pattern candidates."""
