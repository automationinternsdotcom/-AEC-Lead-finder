"""One-time, local-only reconciliation for the interrupted SWVP canary.

This module never calls Pipedrive or Warmy. It preserves external identifiers,
chooses one local event/sequence, and supersedes unsafe legacy enrollment jobs.
Any provider cleanup remains an explicit, separately reviewed action.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .database import Database
from .ids import stable_uuid
from .models import (
    CompanySync,
    EligibilityStatus,
    EventRole,
    LeadEventSync,
    OutreachSequenceSync,
    RecipientSync,
)


SWVP_LEGACY_ORGANIZATION_ID = "682c1480-fdbd-5ba7-a133-ff387d0949c4"
SWVP_EVENT_ID = "63f6316d-ebf2-50f3-bf4a-0dcbd28572e9"
SWVP_PRIMARY_EMAIL = "cmack@swvp.com"
SWVP_EXPECTED_EMAILS = {
    "cmack@swvp.com",
    "jmerritt@swvp.com",
    "mschlossberg@swvp.com",
}
CAMPAIGN_PROTOCOL = "recipient-outreach-v4"


def legacy_swvp_plan(database_path: str | Path) -> dict[str, Any]:
    snapshot = _snapshot(database_path)
    rows = snapshot["mappings"]
    emails = {row["email"] for row in rows}
    if emails != SWVP_EXPECTED_EMAILS:
        raise ValueError(
            f"SWVP reconciliation expected {sorted(SWVP_EXPECTED_EMAILS)}, got {sorted(emails)}"
        )
    primary = next(row for row in rows if row["email"] == SWVP_PRIMARY_EMAIL)
    backups = [row for row in rows if row["email"] != SWVP_PRIMARY_EMAIL]
    return {
        "scope": "Southwest Value Partners interrupted canary",
        "database_path": str(Path(database_path)),
        "local_apply_only": True,
        "company_id": stable_uuid("company", "swvp.com"),
        "lead_event_id": SWVP_EVENT_ID,
        "primary": {
            "email": primary["email"],
            "pipedrive_lead_id": primary["pipedrive_lead_id"],
            "warmy_prospect_id": primary["warmy_prospect_id"],
            "reason": "manual interrupted-canary decision: Cary Mack is the sole primary",
        },
        "backups": [
            {
                "email": row["email"],
                "pipedrive_lead_id": row["pipedrive_lead_id"],
                "warmy_prospect_id": row["warmy_prospect_id"],
                "provider_action": "archive duplicate Lead only after separate approval",
            }
            for row in backups
        ],
        "pending_legacy_enrollment_jobs": snapshot["pending_enrollment_jobs"],
        "local_actions": [
            "create immutable company alias record",
            "preserve the existing event and provider IDs",
            "rank Cary primary and Justin/Mark backups",
            "create a review-only outreach sequence",
            "supersede every pending legacy warmy.enroll job",
        ],
        "provider_writes": [],
        "blocking_reviews": [
            "standalone Warmy verification under the current policy is required",
            "legacy Y-line snapshot requires current campaign-template approval",
            "manual primary override must remain visible in the approval batch",
            "Mark Schlossberg email-source claim requires source review",
        ],
    }


def apply_legacy_swvp_local(database_path: str | Path) -> dict[str, Any]:
    plan = legacy_swvp_plan(database_path)
    snapshot = _snapshot(database_path)
    db = Database(database_path)
    rows = {row["email"]: row for row in snapshot["mappings"]}
    payloads = snapshot["contact_payloads"]
    primary_row = rows[SWVP_PRIMARY_EMAIL]
    primary_payload = payloads[primary_row["outreach_id"]]
    company_id = plan["company_id"]

    company = CompanySync(
        company_id=company_id,
        canonical_name="Southwest Value Partners",
        domain="swvp.com",
        aliases=["SWVP", "Southwest Value Partners"],
        legacy_ids=[SWVP_LEGACY_ORGANIZATION_ID],
    )
    db.upsert_company(company, source="legacy-swvp-reconciliation")
    db.update_company(
        company_id,
        pipedrive_organization_id=int(primary_row["pipedrive_organization_id"]),
    )

    event = LeadEventSync(
        run_id=str(primary_payload.get("run_id") or "legacy-swvp"),
        lead_event_id=SWVP_EVENT_ID,
        company_id=company_id,
        organization_name="Southwest Value Partners",
        event_role=EventRole.ANCHOR,
        event=str(primary_payload.get("event") or "Esplanade III acquisition"),
        location=str(primary_payload.get("location") or "Phoenix"),
        date_posted=str(primary_payload.get("date_posted") or ""),
        summary=str(primary_payload.get("summary") or ""),
        article_url=str(primary_payload.get("article_url") or ""),
        score=95,
        confidence="high",
        record_status="valid",
        actionable_route=True,
        crm_eligible=True,
    )
    db.upsert_lead_event(event)
    db.update_lead_event(
        SWVP_EVENT_ID,
        pipedrive_lead_id=str(primary_row["pipedrive_lead_id"]),
        crm_state="legacy_reconciled_review",
    )

    order = ["cmack@swvp.com", "jmerritt@swvp.com", "mschlossberg@swvp.com"]
    role_scores = {
        "cmack@swvp.com": 72,
        "jmerritt@swvp.com": 77,
        "mschlossberg@swvp.com": 72,
    }
    recipient_ids: dict[str, str] = {}
    for rank, email in enumerate(order, start=1):
        row = rows[email]
        payload = payloads[row["outreach_id"]]
        name = str(payload.get("person_name") or email.split("@", 1)[0])
        recipient_id = stable_uuid("recipient", company_id, row["person_id"])
        recipient_ids[email] = recipient_id
        rationale = [
            "legacy_interrupted_canary_record",
            "manual_primary_override" if rank == 1 else "backup_only_no_enrollment",
        ]
        if email == "mschlossberg@swvp.com":
            rationale.append("legacy_source_claim_requires_review")
        recipient = RecipientSync(
            recipient_id=recipient_id,
            company_id=company_id,
            person_id=row["person_id"],
            contact_candidate_id=row["source_contact_candidate_id"],
            full_name=name,
            first_name=name.split()[0],
            title=str(payload.get("title") or ""),
            scope="Phoenix" if email == "jmerritt@swvp.com" else "",
            email=email,
            source_provider=str(row["source_provider"] or "legacy"),
            source_verification_status=str(
                row["source_verification_status"] or "unknown"
            ),
            source_verification_reason=str(
                payload.get("source_verification_reason") or "legacy_mx_check"
            ),
            role_score=role_scores[email],
            rank=rank,
            primary=rank == 1,
            selection_rationale=rationale,
        )
        db.upsert_recipient(recipient)
        db.update_recipient(
            recipient_id,
            verification_status="pending",
            verification_policy_version="",
            verification_reason="legacy result is not current standalone verification",
            pipedrive_person_id=int(row["pipedrive_person_id"]),
            warmy_prospect_id=str(row["warmy_prospect_id"]),
        )

    personalized = str(primary_payload.get("why_line") or "").strip()
    if not personalized:
        raise ValueError("Cary's legacy personalized Y-line is missing")
    company_why = personalized.replace("Hi Cary", "Hi [first name]", 1)
    merge_snapshot = {
        "firstName": "Cary",
        "company": "Southwest Value Partners",
        "whyLine": personalized,
        "unsubscribeUrl": "__integration_generated__",
    }
    merge_hash = hashlib.sha256(
        json.dumps(
            merge_snapshot, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    sequence_id = stable_uuid("outreach-sequence", company_id, CAMPAIGN_PROTOCOL)
    sequence = OutreachSequenceSync(
        sequence_id=sequence_id,
        run_id=event.run_id,
        company_id=company_id,
        campaign_protocol=CAMPAIGN_PROTOCOL,
        anchor_lead_event_id=SWVP_EVENT_ID,
        primary_recipient_id=recipient_ids[SWVP_PRIMARY_EMAIL],
        why_template_key="legacy_y_line",
        why_slots={"legacy_snapshot": "true"},
        why_sources=[event.article_url] if event.article_url else [],
        why_confidence="medium",
        company_why_line=company_why,
        personalized_why_line=personalized,
        merge_snapshot=merge_snapshot,
        merge_hash=merge_hash,
        eligibility_status=EligibilityStatus.REVIEW,
        eligibility_reasons=[
            "legacy_migration_requires_reverification",
            "legacy_y_line_requires_contract_review",
            "legacy_manual_primary_override",
        ],
    )
    db.save_sequence(sequence)
    db.record_eligibility_decision(
        stable_uuid("eligibility", "legacy-swvp", sequence_id),
        "outreach_sequence",
        sequence_id,
        "review",
        sequence.eligibility_reasons,
        {"source": "interrupted_canary", "primary": SWVP_PRIMARY_EMAIL},
    )

    superseded = 0
    for work_id in snapshot["pending_enrollment_jobs"]:
        superseded += int(
            db.supersede_work(
                work_id,
                "superseded by typed one-company/one-sequence reconciliation",
            )
        )
    db.set_state(
        "legacy-swvp-provider-cleanup-plan",
        {
            "keep_lead_id": primary_row["pipedrive_lead_id"],
            "archive_lead_ids_after_separate_approval": [
                rows[email]["pipedrive_lead_id"] for email in order[1:]
            ],
            "provider_writes_performed": False,
        },
    )
    return {
        **plan,
        "applied_local": True,
        "sequence_id": sequence_id,
        "superseded_enrollment_jobs": superseded,
        "provider_writes_performed": False,
    }


def _snapshot(database_path: str | Path) -> dict[str, Any]:
    path = Path(database_path).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        mappings = [
            dict(row)
            for row in connection.execute(
                """SELECT * FROM lead_mappings
                   WHERE organization_id=? ORDER BY email""",
                (SWVP_LEGACY_ORGANIZATION_ID,),
            )
        ]
        contact_payloads: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            """SELECT payload FROM work_items
               WHERE kind='scout.contact.sync' ORDER BY id DESC"""
        ):
            payload = json.loads(row["payload"])
            outreach_id = str(payload.get("outreach_id") or "")
            if outreach_id and outreach_id not in contact_payloads:
                contact_payloads[outreach_id] = payload
        missing = [
            row["outreach_id"]
            for row in mappings
            if row["outreach_id"] not in contact_payloads
        ]
        if missing:
            raise ValueError(f"legacy contact payloads missing for {missing}")
        pending = [
            int(row["id"])
            for row in connection.execute(
                """SELECT id FROM work_items
                   WHERE kind='warmy.enroll' AND status IN ('pending','running')
                   ORDER BY id"""
            )
        ]
        return {
            "mappings": mappings,
            "contact_payloads": contact_payloads,
            "pending_enrollment_jobs": pending,
        }
    finally:
        connection.close()
