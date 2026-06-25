"""Event-signal pattern wrapper around the current article qualification flow."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from pipeline import extract
from pipeline.patterns.base import PatternCandidate, PatternResult
from pipeline.scoring import score_event_signal
from pipeline.spec import CampaignSpecV2
from schema import ExtractedArticle


class EventSignalPattern:
    pattern_type = "event_signal"

    def run(self, records: list[dict[str, Any]], spec: CampaignSpecV2) -> PatternResult:
        candidates: list[PatternCandidate] = []
        for record in records:
            article = ExtractedArticle.model_validate(record)
            qualified, reason = extract.is_qualifying(article)
            score = score_event_signal(article, spec)
            raw = article.model_dump(mode="json")
            source_url = _source_url(record)
            if source_url:
                raw["url"] = source_url
            candidates.append(
                PatternCandidate(
                    candidate_id=_article_candidate_id(article),
                    pattern_type="event_signal",
                    entity_name=article.company_name,
                    score=score.total,
                    qualified=qualified,
                    filter_reason=reason,
                    evidence={
                        "score": score.model_dump(mode="json"),
                        "signal_type": article.signal_type,
                        "priority": article.priority,
                        "confidence": article.confidence,
                        "city": article.city,
                    },
                    raw=raw,
                )
            )
        return PatternResult.from_records("event_signal", candidates)


def _source_url(record: dict[str, Any]) -> str | None:
    for key in ("url", "article_url", "source_url"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _article_candidate_id(article: ExtractedArticle) -> str:
    key = "|".join(
        str(part or "")
        for part in (
            article.title,
            article.company_name,
            article.published_date,
            article.city,
        )
    )
    return "event:" + sha256(key.encode("utf-8")).hexdigest()[:16]
