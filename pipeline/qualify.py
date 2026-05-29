from __future__ import annotations

from schema import ExtractionResult, QualificationStatus


ALLOWED_CITIES = {
    "phoenix",
    "scottsdale",
    "mesa",
    "tempe",
    "chandler",
    "gilbert",
    "glendale",
    "peoria",
    "surprise",
    "goodyear",
}

ALLOWED_PROPERTY_TYPES = {"office", "retail", "medical", "industrial"}
REJECTED_RECENCY = {"too_far_out"}
REVIEW_RECENCY = {"unknown"}


def qualify(extraction: ExtractionResult) -> tuple[QualificationStatus, str, str | None]:
    """Apply deterministic rules after LLM extraction.

    The model can summarize evidence, but this function owns final routing.
    """
    if not extraction.qualification.is_qualified:
        return "rejected", extraction.qualification.reason, extraction.qualification.reason

    city = (extraction.qualification.city or "").casefold().strip()
    if not city:
        return "review", "city_missing_or_ambiguous", None
    if city not in ALLOWED_CITIES:
        return "rejected", "outside_phoenix_metro", "outside_phoenix_metro"

    if extraction.property_type is None:
        return "review", "property_type_missing_or_ambiguous", None
    if extraction.property_type not in ALLOWED_PROPERTY_TYPES:
        return "rejected", "unsupported_property_type", "unsupported_property_type"

    recency = extraction.qualification.recency_status
    if recency in REJECTED_RECENCY:
        return "rejected", "recency_too_far_out", "recency_too_far_out"
    if recency in REVIEW_RECENCY:
        return "review", "recency_unknown", None

    if not extraction.property_name:
        return "review", "property_name_missing", None

    return "qualified", "qualified_phoenix_metro_commercial_property", None

