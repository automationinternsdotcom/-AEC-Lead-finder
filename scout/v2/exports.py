"""Validated V2 state projections to compatibility CSV, JSONL, and HTML."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import build_email
import csvio

from .artifacts import ArtifactStore
from .contracts import ContactCandidate, DiscoveryCandidate, Organization, Person
from .state import StateStore


LEAD_FIELDS = [
    "link",
    "business_name",
    "person",
    "event",
    "date_posted",
    "location",
    "summary",
    "state",
    "source_site",
    "aka",
    "priority",
    "property_type",
    "service_angle",
    "filter_reason",
    "Decision_Makers",
    "Employee_Count",
    "Decision_Maker_Sources",
    "score",
    "lead_event_id",
    "organization_id",
    "primary_candidate_id",
    "supporting_candidate_ids",
    "run_id",
    "record_status",
    "provenance_json",
]
CONTACT_FIELDS = [
    "business_name",
    "state",
    "location",
    "event",
    "date_posted",
    "summary",
    "link",
    "employee_count",
    "person",
    "title",
    "linkedin",
    "email",
    "phone",
    "sources",
    "score",
    "lead_event_id",
    "organization_id",
    "person_id",
    "contact_candidate_id",
    "verification_status",
    "verification_reason",
    "provider",
    "run_id",
    "record_status",
    "provenance_json",
]
UNCERTAIN_FIELDS = [
    "link",
    "business_name",
    "event",
    "date_posted",
    "location",
    "summary",
    "state",
    "source_site",
    "candidate_id",
    "run_id",
    "record_status",
    "validation_errors",
    "review_id",
    "review_stage",
]


class ExportService:
    def __init__(
        self,
        state: StateStore,
        artifacts: ArtifactStore,
        results_dir: str | Path,
        stamp: str,
    ):
        self.state = state
        self.artifacts = artifacts
        self.results_dir = Path(results_dir)
        self.stamp = stamp
        self.day_dir = self.results_dir / stamp

    def export(self) -> dict[str, object]:
        self.day_dir.mkdir(parents=True, exist_ok=True)
        events = self.state.active_events_for_run(self.artifacts.run_id)
        organizations = {
            item.organization_id: item
            for item in self.state.organizations({event.organization_id for event in events})
        }
        candidates = {
            item.candidate_id: item
            for item in self.state.candidates_for_run(self.artifacts.run_id)
        }
        people = self.state.people()
        people_by_org: dict[str, list[Person]] = defaultdict(list)
        for person in people:
            people_by_org[person.organization_id].append(person)
        contacts = self.state.contacts_for_run(self.artifacts.run_id)
        selected_contacts = {
            (item.lead_event_id, item.person_id): item
            for item in contacts
            if item.selected and item.verification_status.value != "rejected"
        }
        scores = {
            item.lead_event_id: item.score
            for item in self.state.scores_for_run(self.artifacts.run_id)
        }
        lead_rows = [
            self._lead_row(
                event,
                organizations[event.organization_id],
                candidates,
                people_by_org[event.organization_id],
                scores.get(event.lead_event_id),
            )
            for event in events
            if event.organization_id in organizations
        ]
        lead_rows.sort(key=lambda row: (-_score(row.get("score")), row["lead_event_id"]))
        contact_rows = []
        for event in events:
            organization = organizations.get(event.organization_id)
            primary = candidates.get(event.primary_candidate_id)
            if not organization:
                continue
            for person in people_by_org[event.organization_id]:
                contact_rows.append(
                    self._contact_row(
                        event,
                        organization,
                        person,
                        selected_contacts.get((event.lead_event_id, person.person_id)),
                        primary,
                        scores.get(event.lead_event_id),
                    )
                )
        contact_rows.sort(key=lambda row: (-_score(row.get("score")), row["lead_event_id"], row["person_id"]))
        uncertain_rows = self._uncertain_rows(candidates)

        raw_path = self.day_dir / "raw_leads.csv"
        contacts_path = self.day_dir / "contacts.csv"
        uncertain_path = self.day_dir / "uncertain_leads.csv"
        csvio.write_csv(str(raw_path), lead_rows, LEAD_FIELDS)
        csvio.write_csv(str(contacts_path), contact_rows, CONTACT_FIELDS)
        csvio.write_csv(str(uncertain_path), uncertain_rows, UNCERTAIN_FIELDS)
        artifacts = [
            self.artifacts.record_existing("export", "csv", raw_path),
            self.artifacts.record_existing("export", "csv", contacts_path),
            self.artifacts.record_existing("export", "csv", uncertain_path),
            self.artifacts.write_jsonl("export", "lead_events.jsonl", events),
            self.artifacts.write_jsonl("export", "contacts.jsonl", contacts),
            self.artifacts.write_jsonl(
                "export", "reviews.jsonl", self.state.reviews_for_run(self.artifacts.run_id)
            ),
        ]
        html_path = Path(build_email.build(self.stamp, str(self.results_dir)))
        artifacts.append(self.artifacts.record_existing("export", "html", html_path))
        return {
            "lead_count": len(lead_rows),
            "contact_count": len(contact_rows),
            "review_count": len(uncertain_rows),
            "paths": {
                "raw_leads": str(raw_path),
                "contacts": str(contacts_path),
                "uncertain_leads": str(uncertain_path),
                "html": str(html_path),
            },
            "artifacts": artifacts,
        }

    def _lead_row(self, event, organization, candidates, people, score):
        primary = candidates.get(event.primary_candidate_id)
        source_urls = [
            candidates[item].canonical_url
            for item in event.supporting_candidate_ids
            if item in candidates
        ]
        decision_sources = list(
            dict.fromkeys(
                evidence.url for person in people for evidence in person.evidence
            )
        )
        return {
            "link": primary.canonical_url if primary else (source_urls[0] if source_urls else ""),
            "business_name": organization.canonical_name,
            "person": people[0].name if people else "",
            "event": event.event,
            "date_posted": str(event.date_posted or ""),
            "location": event.location,
            "summary": event.summary,
            "state": event.state,
            "source_site": _site(primary.canonical_url) if primary else "",
            "aka": ", ".join(organization.aliases),
            "priority": event.priority,
            "property_type": event.property_type,
            "service_angle": event.service_angle,
            "filter_reason": event.filter_reason,
            "Decision_Makers": "; ".join(_person_label(person) for person in people),
            "Employee_Count": _employee_count(organization),
            "Decision_Maker_Sources": " ".join(decision_sources),
            "score": "" if score is None else str(score),
            "lead_event_id": event.lead_event_id,
            "organization_id": event.organization_id,
            "primary_candidate_id": event.primary_candidate_id,
            "supporting_candidate_ids": ",".join(event.supporting_candidate_ids),
            "run_id": event.run_id,
            "record_status": event.record_status.value,
            "provenance_json": json.dumps(
                {"source_urls": source_urls, "evidence": [item.model_dump(mode="json") for item in event.evidence]},
                sort_keys=True,
                default=str,
            ),
        }

    def _contact_row(self, event, organization, person, contact, primary, score):
        evidence = contact.evidence if contact else person.evidence
        return {
            "business_name": organization.canonical_name,
            "state": event.state,
            "location": event.location,
            "event": event.event,
            "date_posted": str(event.date_posted or ""),
            "summary": event.summary,
            "link": primary.canonical_url if primary else "",
            "employee_count": _employee_count(organization),
            "person": person.name,
            "title": person.title,
            "linkedin": contact.linkedin if contact else "",
            "email": contact.email if contact else "",
            "phone": contact.phone if contact else "",
            "sources": " ".join(item.url for item in evidence),
            "score": "" if score is None else str(score),
            "lead_event_id": event.lead_event_id,
            "organization_id": organization.organization_id,
            "person_id": person.person_id,
            "contact_candidate_id": contact.contact_candidate_id if contact else "",
            "verification_status": contact.verification_status.value if contact else "unknown",
            "verification_reason": contact.verification_reason if contact else "no_contact_candidate",
            "provider": contact.provider if contact else "",
            "run_id": event.run_id,
            "record_status": "valid" if contact else "review",
            "provenance_json": json.dumps(
                [item.model_dump(mode="json") for item in evidence], sort_keys=True, default=str
            ),
        }

    def _uncertain_rows(self, candidates: dict[str, DiscoveryCandidate]) -> list[dict]:
        rows = []
        for review in self.state.reviews_for_run(self.artifacts.run_id):
            candidate = candidates.get(review.record_id)
            if not candidate:
                continue
            rows.append(
                {
                    "link": candidate.canonical_url,
                    "business_name": candidate.title,
                    "event": "",
                    "date_posted": str(candidate.published_at.date()) if candidate.published_at else "",
                    "location": "",
                    "summary": "",
                    "state": "Arizona",
                    "source_site": _site(candidate.canonical_url),
                    "candidate_id": candidate.candidate_id,
                    "run_id": candidate.run_id,
                    "record_status": candidate.record_status.value,
                    "validation_errors": " | ".join(review.validation_errors),
                    "review_id": review.review_id,
                    "review_stage": review.stage,
                }
            )
        return rows


def _site(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _person_label(person: Person) -> str:
    title = f" — {person.title}" if person.title else ""
    scope = f" ({person.scope})" if person.scope else ""
    return f"{person.name}{title}{scope}"


def _employee_count(organization: Organization) -> str:
    value = organization.employee_count or {}
    count = str(value.get("value") or "").strip()
    detail = ", ".join(
        str(value.get(key) or "").strip()
        for key in ("scope", "as_of")
        if str(value.get(key) or "").strip()
    )
    return f"{count} ({detail})" if count and detail else count


def _score(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
