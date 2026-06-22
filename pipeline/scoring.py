"""Deterministic scoring helpers for Phase 2 pattern modules.

Scores are advisory ranking signals, not qualification gates. The current
Phase 1 drop rules still own whether an article can proceed.
"""
from __future__ import annotations

from statistics import mean
from typing import Iterable

from pydantic import BaseModel, Field

from pipeline.spec import CampaignSpecV2
from schema import ExtractedArticle


class ScoreBreakdown(BaseModel):
    total: int = Field(ge=0, le=100)
    confidence_points: int = 0
    priority_points: int = 0
    signal_points: int = 0
    geography_points: int = 0
    evidence_points: int = 0
    penalty_points: int = 0
    reasons: list[str] = Field(default_factory=list)


def score_event_signal(article: ExtractedArticle, spec: CampaignSpecV2) -> ScoreBreakdown:
    """Score one extracted article without changing existing qualification."""
    confidence_points = round(max(0.0, min(article.confidence, 1.0)) * 45)
    priority_points = {"high": 25, "medium": 12, "low": 0}[article.priority]
    signal_points = 15 if _has_trigger_signal(article, spec) else 5
    geography_points = 10 if article.az_relevant else 0

    penalties = 0
    reasons: list[str] = []
    if not article.az_relevant:
        penalties += 35
        reasons.append("property outside campaign geography")
    if article.priority == "low":
        penalties += 25
        reasons.append("low priority per campaign rubric")
    if article.confidence < spec.quality_gates.min_confidence:
        penalties += 15
        reasons.append("below campaign confidence gate")
    if article.signal_type == "other":
        penalties += 5
        reasons.append("generic signal type")

    total = _clamp(
        confidence_points
        + priority_points
        + signal_points
        + geography_points
        - penalties
    )
    if not reasons:
        reasons.append("matches event-signal scoring criteria")

    return ScoreBreakdown(
        total=total,
        confidence_points=confidence_points,
        priority_points=priority_points,
        signal_points=signal_points,
        geography_points=geography_points,
        penalty_points=penalties,
        reasons=reasons,
    )


def score_entity_aggregate(
    *,
    confidences: Iterable[float],
    signal_count: int,
    observation_count: int,
    needs_adjudication: bool,
    spec: CampaignSpecV2,
) -> ScoreBreakdown:
    """Score an aggregated entity from deterministic evidence counts."""
    confidence_values = [max(0.0, min(float(v), 1.0)) for v in confidences]
    avg_confidence = mean(confidence_values) if confidence_values else 0.0
    confidence_points = round(avg_confidence * 45)
    evidence_points = min(observation_count, 5) * 7
    signal_points = min(signal_count, 3) * 8
    penalties = 20 if needs_adjudication else 0
    if avg_confidence < spec.quality_gates.min_confidence:
        penalties += 15

    reasons = [
        f"{observation_count} observation(s)",
        f"{signal_count} unique signal(s)",
    ]
    if needs_adjudication:
        reasons.append("requires Codex adjudication before enrichment")
    if avg_confidence < spec.quality_gates.min_confidence:
        reasons.append("below campaign confidence gate")

    return ScoreBreakdown(
        total=_clamp(confidence_points + evidence_points + signal_points - penalties),
        confidence_points=confidence_points,
        evidence_points=evidence_points,
        signal_points=signal_points,
        penalty_points=penalties,
        reasons=reasons,
    )


def sort_by_score_desc(records: list[dict]) -> list[dict]:
    """Return records ordered by score desc, preserving stable tie order."""
    return sorted(records, key=lambda r: r.get("score", 0), reverse=True)


def _has_trigger_signal(article: ExtractedArticle, spec: CampaignSpecV2) -> bool:
    haystack = " ".join(
        str(part or "")
        for part in (
            article.signal_type,
            article.title,
            article.summary_2sent,
            article.filter_reason,
        )
    ).lower()
    if article.signal_type != "other":
        return True
    return any(_token_hit(signal, haystack) for signal in spec.signals.trigger_signals)


def _token_hit(signal: str, haystack: str) -> bool:
    tokens = [t for t in signal.lower().replace("/", " ").split() if len(t) >= 4]
    return bool(tokens) and any(token in haystack for token in tokens)


def _clamp(value: int) -> int:
    return max(0, min(100, int(value)))
