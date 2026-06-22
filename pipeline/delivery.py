"""Convert artifacts into destination preview records."""
from __future__ import annotations

from pipeline.contracts import ArtifactEnvelope
from pipeline.destinations.base import DeliveryRecord


def delivery_records_from_artifact(artifact: ArtifactEnvelope) -> list[DeliveryRecord]:
    return [delivery_record_from_candidate(record) for record in artifact.records]


def delivery_record_from_candidate(record: dict) -> DeliveryRecord:
    evidence = record.get("evidence") or {}
    raw = record.get("raw") or {}
    lead = record.get("lead") or raw.get("lead") or {}
    return DeliveryRecord(
        title=raw.get("title") or record.get("entity_name") or record.get("company_name") or "Untitled lead",
        company_name=record.get("entity_name") or raw.get("company_name") or record.get("company_name") or "",
        url=raw.get("url") or record.get("url"),
        priority=raw.get("priority"),
        score=record.get("score"),
        contact_name=lead.get("name"),
        contact_title=lead.get("title"),
        contact_email=lead.get("email"),
        contact_phone=lead.get("phone"),
        notes=record.get("adjudication_reason") or record.get("filter_reason"),
        raw=record,
    )
