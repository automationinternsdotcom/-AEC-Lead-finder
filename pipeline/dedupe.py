from __future__ import annotations

import sqlite3

from schema import DedupeResult, LeadRecord
from pipeline import db
from pipeline.util import normalize_key


def build_dedupe_key(lead: LeadRecord) -> str:
    parts = [
        normalize_key(lead.property_name),
        normalize_key(lead.address),
        normalize_key(lead.property_type),
    ]
    return "|".join(part for part in parts if part)


def check_local_duplicates(conn: sqlite3.Connection, lead: LeadRecord) -> DedupeResult:
    existing_by_url = db.find_lead_by_source_url(conn, lead.source_url)
    if existing_by_url and existing_by_url.lead_id != lead.lead_id:
        return DedupeResult(
            outcome="exact_duplicate",
            reason="source_url_already_seen",
            existing_lead_id=existing_by_url.lead_id,
        )

    if lead.dedupe_key:
        existing_by_key = db.find_lead_by_dedupe_key(conn, lead.dedupe_key)
        if existing_by_key and existing_by_key.lead_id != lead.lead_id:
            return DedupeResult(
                outcome="probable_duplicate",
                reason="same_property_name_address_type",
                existing_lead_id=existing_by_key.lead_id,
            )

    return DedupeResult(outcome="new", reason="no_local_duplicate_found")

