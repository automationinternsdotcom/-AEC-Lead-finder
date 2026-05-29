from __future__ import annotations

from schema import EnrichmentResult, ExtractionResult, ScoreBreakdown, ScoreLine


def score_lead(
    extraction: ExtractionResult,
    enrichment: EnrichmentResult | None = None,
    *,
    possible_duplicate: bool = False,
) -> ScoreBreakdown:
    lines: list[ScoreLine] = []

    def add(signal: str, points: int, reason: str) -> None:
        lines.append(ScoreLine(signal=signal, points=points, reason=reason))

    if extraction.property_type:
        add("qualified_property_type", 20, f"{extraction.property_type} is in scope")
    if extraction.qualification.city:
        add("phoenix_metro_location", 20, f"{extraction.qualification.city} is in scope")
    if extraction.qualification.recency_status != "unknown":
        add("valid_recency_signal", 20, extraction.qualification.recency_status)
    if extraction.property_name:
        add("property_name_present", 10, "property name extracted")
    if extraction.address:
        add("address_present", 5, "address or location extracted")
    if extraction.has_article_contact:
        add("article_contact_found", 10, "source article includes contact details")

    best_contact = enrichment.best_contact if enrichment else None
    if best_contact and best_contact.has_contact and best_contact.confidence >= 0.75:
        add("high_confidence_enriched_contact", 8, "enrichment returned a strong contact")

    if len(extraction.evidence) >= 2:
        add("source_evidence_complete", 5, "field evidence is available")

    if not extraction.has_article_contact:
        add("missing_contact", -10, "article did not include contact details")
    if extraction.qualification.recency_status == "unknown":
        add("unclear_opening_status", -10, "opening or status is unclear")
    if possible_duplicate:
        add("possible_duplicate", -25, "similar lead already exists")
    if extraction.confidence < 0.6:
        add("weak_source_confidence", -15, "extraction confidence below threshold")

    score = max(0, min(100, sum(line.points for line in lines)))
    if score >= 60:
        route = "review"
    elif score >= 40:
        route = "store_only"
    else:
        route = "reject"
    return ScoreBreakdown(score=score, route=route, lines=lines)

