"""Normalize Scout contact CSV rows into person-level integration jobs."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .ids import event_id, organization_id, person_id, stable_uuid
from .models import ContactSync


def contacts_from_csv(path: str | Path, run_id: str) -> list[ContactSync]:
    with Path(path).open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    seen_by_event: defaultdict[str, int] = defaultdict(int)
    contacts: list[ContactSync] = []
    for row in rows:
        email = str(row.get("email") or "").strip().casefold()
        name = str(row.get("person") or "").strip()
        business = str(row.get("business_name") or "").strip()
        if not email or not name or not business:
            continue
        location = str(row.get("location") or "").strip()
        event = str(row.get("event") or "").strip()
        date_posted = str(row.get("date_posted") or "").strip()
        lead_id = str(row.get("lead_event_id") or "") or event_id(
            business, event, location, date_posted
        )
        org_id = str(row.get("organization_id") or "") or organization_id(
            business, "", location
        )
        person_stable_id = str(row.get("person_id") or "") or person_id(name, business)
        source_contact_id = str(row.get("contact_candidate_id") or "")
        outreach_id = stable_uuid("sales-outreach", lead_id, person_stable_id)
        row_run_id = str(row.get("run_id") or "")
        if row_run_id and row_run_id != run_id:
            raise ValueError(
                f"contacts CSV run_id {row_run_id!r} does not match {run_id!r}"
            )
        event_key = lead_id
        is_primary = seen_by_event[event_key] == 0
        seen_by_event[event_key] += 1
        contacts.append(
            ContactSync(
                run_id=run_id,
                lead_event_id=lead_id,
                organization_id=org_id,
                person_id=person_stable_id,
                outreach_id=outreach_id,
                source_contact_candidate_id=source_contact_id,
                source_verification_status=str(
                    row.get("verification_status") or "unknown"
                ),
                source_verification_reason=str(row.get("verification_reason") or ""),
                source_provider=str(row.get("provider") or ""),
                organization_name=business,
                person_name=name,
                email=email,
                title=str(row.get("title") or "").strip(),
                phone=str(row.get("phone") or "").strip(),
                linkedin=str(row.get("linkedin") or "").strip(),
                location=location,
                event=event,
                article_url=str(row.get("link") or "").strip(),
                date_posted=date_posted,
                summary=str(row.get("summary") or "").strip(),
                why_line=str(row.get("why_line") or "").strip(),
                is_primary=is_primary,
                source_payload={
                    key: value
                    for key, value in row.items()
                    if key not in {"email", "phone"} and value not in (None, "")
                },
            )
        )
    return contacts


def enqueue_contacts(db, path: str | Path, run_id: str) -> tuple[int, int]:
    created = 0
    contacts = contacts_from_csv(path, run_id)
    for contact in contacts:
        payload = contact.model_dump(mode="json")
        material = {
            key: value
            for key, value in payload.items()
            if key not in {"run_id", "source_payload"}
        }
        revision = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        if db.enqueue_work(
            "scout.contact.sync",
            f"scout:contact:{contact.outreach_id}:{revision}",
            payload,
        ):
            created += 1
    db.record_run(run_id, str(Path(path)), len(contacts), created)
    return created, len(contacts)
