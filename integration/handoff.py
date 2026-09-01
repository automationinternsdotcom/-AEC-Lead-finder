"""Validated Scout-to-sales handoff ingestion.

The handoff is the only production boundary accepted by the provider worker.
Compatibility CSV files remain analytical exports and are never provider input.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .ids import stable_uuid
from .models import EligibilityStatus, SalesHandoff


HANDOFF_SCHEMA_VERSION = 1
HANDOFF_PROTOCOL_VERSION = "aether-sales-handoff-v1"


def handoff_content_hash(value: SalesHandoff | dict) -> str:
    payload = (
        value.model_dump(mode="json")
        if isinstance(value, SalesHandoff)
        else dict(value)
    )
    payload.pop("content_hash", None)
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def load_handoff(path: str | Path) -> SalesHandoff:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    handoff = SalesHandoff.model_validate(payload)
    if handoff.schema_version != HANDOFF_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported handoff schema {handoff.schema_version}; "
            f"expected {HANDOFF_SCHEMA_VERSION}"
        )
    if handoff.protocol_version != HANDOFF_PROTOCOL_VERSION:
        raise ValueError(
            f"unsupported handoff protocol {handoff.protocol_version!r}"
        )
    actual = handoff_content_hash(handoff)
    if actual != handoff.content_hash:
        raise ValueError(
            f"handoff content hash mismatch: expected {handoff.content_hash}, got {actual}"
        )
    return handoff


def ingest_handoff(db, handoff: SalesHandoff, *, source_file: str = "") -> dict[str, int]:
    companies = {item.company_id: item for item in handoff.companies}
    events = {item.lead_event_id: item for item in handoff.lead_events}
    recipients = {item.recipient_id: item for item in handoff.recipients}

    if len(companies) != len(handoff.companies):
        raise ValueError("handoff contains duplicate company IDs")
    if len(events) != len(handoff.lead_events):
        raise ValueError("handoff contains duplicate lead event IDs")
    if len(recipients) != len(handoff.recipients):
        raise ValueError("handoff contains duplicate recipient IDs")

    for event in events.values():
        if event.company_id not in companies:
            raise ValueError(f"lead event {event.lead_event_id} references missing company")
    for recipient in recipients.values():
        if recipient.company_id not in companies:
            raise ValueError(f"recipient {recipient.recipient_id} references missing company")
    for sequence in handoff.sequences:
        if sequence.company_id not in companies:
            raise ValueError(f"sequence {sequence.sequence_id} references missing company")
        if sequence.anchor_lead_event_id not in events:
            raise ValueError(f"sequence {sequence.sequence_id} references missing anchor")
        if sequence.primary_recipient_id not in recipients:
            raise ValueError(f"sequence {sequence.sequence_id} references missing primary")
        if events[sequence.anchor_lead_event_id].company_id != sequence.company_id:
            raise ValueError(f"sequence {sequence.sequence_id} anchor company mismatch")
        if recipients[sequence.primary_recipient_id].company_id != sequence.company_id:
            raise ValueError(f"sequence {sequence.sequence_id} recipient company mismatch")

    for company in handoff.companies:
        db.upsert_company(company, source=f"handoff:{handoff.run_id}")
    for event in handoff.lead_events:
        db.upsert_lead_event(event)
    for recipient in handoff.recipients:
        db.upsert_recipient(recipient)
    for sequence in handoff.sequences:
        db.save_sequence(sequence)

    lead_jobs = 0
    sequence_jobs = 0
    for event in handoff.lead_events:
        if not event.crm_eligible:
            db.record_eligibility_decision(
                stable_uuid("eligibility", handoff.run_id, "lead_event", event.lead_event_id),
                "lead_event",
                event.lead_event_id,
                "blocked",
                event.crm_exclusion_reasons,
                {"score": event.score, "confidence": event.confidence},
            )
            continue
        if db.enqueue_work(
            "scout.lead.sync",
            f"scout:lead:{event.lead_event_id}:{handoff.content_hash[:16]}",
            {"lead_event": event.model_dump(mode="json")},
        ):
            lead_jobs += 1

    for sequence in handoff.sequences:
        if sequence.eligibility_status != EligibilityStatus.READY:
            db.record_eligibility_decision(
                stable_uuid("eligibility", handoff.run_id, "sequence", sequence.sequence_id),
                "outreach_sequence",
                sequence.sequence_id,
                sequence.eligibility_status.value,
                sequence.eligibility_reasons,
                {"merge_hash": sequence.merge_hash},
            )
            continue
        if db.enqueue_work(
            "scout.sequence.sync",
            f"scout:sequence:{sequence.sequence_id}:{sequence.merge_hash[:16]}",
            {
                "sequence": sequence.model_dump(mode="json"),
                "recipient": recipients[sequence.primary_recipient_id].model_dump(
                    mode="json"
                ),
            },
        ):
            sequence_jobs += 1

    db.record_run(
        handoff.run_id,
        source_file,
        len(handoff.lead_events) + len(handoff.sequences),
        lead_jobs + sequence_jobs,
    )
    return {
        "companies": len(handoff.companies),
        "lead_events": len(handoff.lead_events),
        "recipients": len(handoff.recipients),
        "sequences": len(handoff.sequences),
        "lead_jobs": lead_jobs,
        "sequence_jobs": sequence_jobs,
    }


def enqueue_handoff(db, path: str | Path) -> dict[str, int]:
    handoff = load_handoff(path)
    return ingest_handoff(db, handoff, source_file=str(Path(path)))
