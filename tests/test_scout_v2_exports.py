"""Compatibility projections are ID-keyed, atomic, and lossless."""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.artifacts import ArtifactStore  # noqa: E402
from v2.contracts import (  # noqa: E402
    ContactCandidate,
    DiscoveryCandidate,
    Evidence,
    LeadEvent,
    LeadScore,
    Organization,
    Person,
    VerificationStatus,
)
from v2.exports import ExportService, LEAD_FIELDS  # noqa: E402
from v2.state import StateStore  # noqa: E402


def test_export_preserves_legacy_columns_and_separates_same_name_events(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    store.upsert_source("source-1", "Example", "https://example.com/", "example.com")
    evidence = [Evidence(url="https://example.com/source", supports="Source")]
    for index, person_name in ((1, "Jane Manager"), (2, "Joe Operator")):
        candidate = DiscoveryCandidate(
            candidate_id=f"candidate-{index}",
            run_id="run-1",
            provider="curated",
            discovered_url=f"https://example.com/{index}",
            resolved_url=f"https://example.com/{index}",
            canonical_url=f"https://example.com/{index}",
            title="Opening",
            source_id="source-1",
            source_name="Example",
            source_domain="example.com",
            published_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
        store.save_candidate(candidate)
        organization = Organization(
            organization_id=f"org-{index}",
            canonical_name="Shared Business Name",
            location=f"Phoenix Site {index}, Arizona",
            employee_count={"value": "100", "scope": "company", "as_of": "2026"},
            evidence=evidence,
        )
        store.save_organization(organization)
        person = Person(
            person_id=f"person-{index}",
            organization_id=organization.organization_id,
            name=person_name,
            title="Manager",
            evidence=evidence,
        )
        store.save_person(person)
        event = LeadEvent(
            lead_event_id=f"event-{index}",
            run_id="run-1",
            organization_id=organization.organization_id,
            primary_candidate_id=candidate.candidate_id,
            supporting_candidate_ids=[candidate.candidate_id],
            event=f"Opened site {index}.",
            location=organization.location,
            date_posted=date(2026, 8, 28),
            priority="high",
            evidence=evidence,
        )
        store.save_lead_event(event)
        contact = ContactCandidate(
            contact_candidate_id=f"contact-{index}",
            run_id="run-1",
            lead_event_id=event.lead_event_id,
            organization_id=organization.organization_id,
            person_id=person.person_id,
            person_name=person.name,
            title=person.title,
            email=f"person{index}@example.com",
            provider="model",
            verification_status=VerificationStatus.VERIFIED,
            verification_reason="mx_valid",
            selected=True,
            evidence=evidence,
        )
        store.save_contact(contact)
        store.save_score(
            LeadScore(
                run_id="run-1",
                lead_event_id=event.lead_event_id,
                score=0 if index == 1 else 90,
                model="grok-4.3",
                attempt_id="attempt-1",
            )
        )
    artifacts = ArtifactStore(tmp_path / "results", "2026-08-28", "run-1", store)
    output = ExportService(store, artifacts, tmp_path / "results", "2026-08-28").export()

    with Path(output["paths"]["raw_leads"]).open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        assert reader.fieldnames == LEAD_FIELDS
    assert len(rows) == 2
    assert {row["score"] for row in rows} == {"0", "90"}
    assert {row["lead_event_id"] for row in rows} == {"event-1", "event-2"}
    html = Path(output["paths"]["html"]).read_text()
    assert html.count("Jane Manager") == 1
    assert html.count("Joe Operator") == 1
    assert not list((tmp_path / "results").rglob("*.tmp"))
