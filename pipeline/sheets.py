from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from schema import LeadRecord, ReviewDecision, SourceRecord


SOURCE_COLUMNS = [
    "source_id",
    "site_name",
    "source_type",
    "source_url",
    "rss_url",
    "crawl_start_url",
    "allowed_paths",
    "city_scope",
    "status",
    "last_checked_at",
    "last_success_at",
    "last_error",
    "notes",
]

REVIEW_COLUMNS = [
    "review_status",
    "reviewer",
    "reviewed_at",
    "lead_id",
    "lead_score",
    "property_name",
    "address",
    "property_type",
    "opening_date_or_status",
    "original_contact",
    "enriched_contact",
    "enrichment_confidence",
    "source_url",
    "qualification_reason",
    "rejection_reason",
    "pipedrive_status",
    "pipedrive_lead_id",
    "next_action",
]


class CsvSheetGateway:
    """Small sheet-like adapter.

    The production Google Sheets adapter can implement the same methods later.
    CSV keeps the first implementation easy to run and easy to test.
    """

    def __init__(self, source_csv: Path | None, review_csv: Path | None) -> None:
        self.source_csv = source_csv
        self.review_csv = review_csv

    def load_sources(self) -> list[SourceRecord]:
        if not self.source_csv or not self.source_csv.exists():
            return []
        rows = _read_csv(self.source_csv)
        return [SourceRecord.model_validate(row) for row in rows]

    def update_source_status(
        self,
        source_id: str,
        *,
        last_checked_at: str,
        last_success_at: str | None = None,
        last_error: str | None = None,
    ) -> None:
        if not self.source_csv or not self.source_csv.exists():
            return

        rows = _read_csv(self.source_csv)
        for row in rows:
            if row.get("source_id") == source_id:
                row["last_checked_at"] = last_checked_at
                if last_success_at:
                    row["last_success_at"] = last_success_at
                row["last_error"] = last_error or ""
        _write_csv(self.source_csv, SOURCE_COLUMNS, rows)

    def upsert_review_leads(self, leads: Iterable[LeadRecord]) -> None:
        if not self.review_csv:
            return

        existing = {row.get("lead_id"): row for row in _read_csv(self.review_csv)}
        for lead in leads:
            row = _lead_to_review_row(lead)
            current = existing.get(lead.lead_id)
            if current and current.get("review_status") not in {"", "new"}:
                row["review_status"] = current["review_status"]
                row["reviewer"] = current.get("reviewer", "")
                row["reviewed_at"] = current.get("reviewed_at", "")
                row["next_action"] = current.get("next_action", "")
            existing[lead.lead_id] = row

        _write_csv(self.review_csv, REVIEW_COLUMNS, existing.values())

    def load_review_decisions(self) -> list[ReviewDecision]:
        if not self.review_csv or not self.review_csv.exists():
            return []

        decisions: list[ReviewDecision] = []
        for row in _read_csv(self.review_csv):
            status = row.get("review_status") or "new"
            if status == "new":
                continue
            decisions.append(
                ReviewDecision(
                    lead_id=row["lead_id"],
                    review_status=status,
                    reviewer=row.get("reviewer") or None,
                    reviewed_at=row.get("reviewed_at") or None,
                    next_action=row.get("next_action") or None,
                )
            )
        return decisions


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _lead_to_review_row(lead: LeadRecord) -> dict[str, str]:
    enriched = lead.enrichment.best_contact if lead.enrichment else None
    original_contact = " / ".join(
        item for item in [lead.contact_name, lead.contact_title, lead.contact_email, lead.contact_phone] if item
    )
    enriched_contact = ""
    enrichment_confidence = ""
    if enriched:
        enriched_contact = " / ".join(
            item for item in [enriched.name, enriched.title, enriched.email, enriched.phone] if item
        )
        enrichment_confidence = str(enriched.confidence)

    return {
        "review_status": "new",
        "reviewer": "",
        "reviewed_at": "",
        "lead_id": lead.lead_id,
        "lead_score": str(lead.confidence_score),
        "property_name": lead.property_name or "",
        "address": lead.address or "",
        "property_type": lead.property_type or "",
        "opening_date_or_status": lead.opening_date_or_status or "",
        "original_contact": original_contact,
        "enriched_contact": enriched_contact,
        "enrichment_confidence": enrichment_confidence,
        "source_url": lead.source_url,
        "qualification_reason": lead.qualification_reason,
        "rejection_reason": lead.rejection_reason or "",
        "pipedrive_status": lead.pipedrive_status,
        "pipedrive_lead_id": lead.pipedrive_lead_id or "",
        "next_action": "",
    }

