"""Stable-person research and deterministic contact verification."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.artifacts import ArtifactStore  # noqa: E402
from v2.contracts import (  # noqa: E402
    Evidence,
    LeadEvent,
    Organization,
    Person,
    VerificationStatus,
)
from v2.research import ContactResearchService, DecisionMakerService  # noqa: E402
from v2.state import StateStore  # noqa: E402
from v2.verification import ContactVerifier, select_best  # noqa: E402


def setup(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    artifacts = ArtifactStore(tmp_path / "results", "2026-08-28", "run-1", store)
    evidence = [Evidence(url="https://example.com/story", supports="Source event")]
    organization = Organization(
        organization_id="org-1",
        canonical_name="Acme Marketplace",
        domain="acme.com",
        location="Phoenix, Arizona",
        evidence=evidence,
    )
    store.save_organization(organization)
    return store, artifacts, organization, evidence


def test_verifier_rejects_disposable_and_caches_mx(tmp_path):
    store, _, _, _ = setup(tmp_path)
    calls = []
    verifier = ContactVerifier(store, mx_lookup=lambda domain: calls.append(domain) or True)

    rejected = verifier.verify(email="person@mailinator.com")
    assert rejected.status == VerificationStatus.REJECTED
    assert rejected.reason == "email_domain_disposable"
    first = verifier.verify(email="Person@Acme.com", organization_domain="acme.com")
    second = verifier.verify(email="person@acme.com", organization_domain="acme.com")
    assert first.status == second.status == VerificationStatus.VERIFIED
    assert calls == ["acme.com"]


def test_decision_makers_require_sources_and_persist_people(tmp_path):
    store, artifacts, organization, _ = setup(tmp_path)
    payload = {
        "decision_makers": [{"name": "Jane Manager", "title": "General Manager", "scope": "Phoenix"}],
        "employee_count": {"value": "100", "scope": "company", "as_of": "2026"},
        "sources": [{"url": "https://acme.com/team", "supports": "Lists Jane as GM."}],
    }
    service = DecisionMakerService(
        store,
        artifacts,
        "grok-4.3",
        call_model=lambda model, prompt, tools: (json.dumps(payload), {"total_tokens": 50}),
    )
    people, reviews = service.research([organization])

    assert not reviews
    assert people[0].name == "Jane Manager"
    assert store.people("org-1")[0].title == "General Manager"


def test_contact_research_runs_once_per_person_and_projects_to_events(tmp_path):
    store, artifacts, organization, evidence = setup(tmp_path)
    person = Person(
        person_id="person-1",
        organization_id="org-1",
        name="Jane Manager",
        title="General Manager",
        evidence=evidence,
    )
    store.save_person(person)
    events = [
        LeadEvent(
            lead_event_id=f"event-{index}",
            run_id="run-1",
            organization_id="org-1",
            primary_candidate_id=f"candidate-{index}",
            supporting_candidate_ids=[f"candidate-{index}"],
            event=f"Event {index}",
            location="Phoenix, Arizona",
            date_posted=date(2026, 8, 28),
            priority="high",
            evidence=evidence,
        )
        for index in (1, 2)
    ]
    # The event FK requires discovery candidate rows; disable FK only for this focused service fixture.
    with store.connect() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        for event in events:
            conn.execute(
                """INSERT INTO v2_lead_events(
                    lead_event_id, run_id, organization_id, primary_candidate_id,
                    record_status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'valid', ?, 'now', 'now')""",
                (event.lead_event_id, event.run_id, event.organization_id, event.primary_candidate_id, event.model_dump_json()),
            )
        conn.commit()
    calls = []
    payload = {
        "name": "Jane Manager",
        "organization": "Acme Marketplace",
        "email": "jane@acme.com",
        "phone": "",
        "linkedin": "https://linkedin.com/in/jane-manager",
        "sources": [{"url": "https://acme.com/team", "supports": "Lists Jane's contact details."}],
    }
    service = ContactResearchService(
        store,
        artifacts,
        "grok-4.3",
        ContactVerifier(store, mx_lookup=lambda domain: True),
        call_model=lambda model, prompt, tools: calls.append(prompt) or (json.dumps(payload), {}),
    )
    contacts, reviews = service.research([person], [organization], events)

    assert not reviews and len(calls) == 1
    assert len(contacts) == 2
    assert {item.lead_event_id for item in contacts} == {"event-1", "event-2"}
    assert all(item.selected and item.verification_status == VerificationStatus.VERIFIED for item in contacts)


def test_mismatched_contact_identity_enters_review(tmp_path):
    store, artifacts, organization, evidence = setup(tmp_path)
    person = Person(
        person_id="person-1",
        organization_id="org-1",
        name="Jane Manager",
        evidence=evidence,
    )
    service = ContactResearchService(
        store,
        artifacts,
        "grok-4.3",
        ContactVerifier(store, mx_lookup=lambda domain: True),
        call_model=lambda model, prompt, tools: (
            json.dumps({"name": "Different Person", "organization": "Acme Marketplace"}),
            {},
        ),
    )
    contacts, reviews = service.research([person], [organization], [], attempts=1)

    assert not contacts
    assert reviews[0].reason_code == "model_contract_invalid"
