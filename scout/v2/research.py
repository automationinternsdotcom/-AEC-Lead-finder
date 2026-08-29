"""Stable-identity decision-maker and contact research services."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .artifacts import ArtifactStore
from .contracts import ContactCandidate, Evidence, LeadEvent, Organization, Person, ReviewItem
from .ids import normalize_text, person_id, stable_hash, stable_uuid
from .state import StateStore
from .verification import ContactVerifier, select_best


ModelCall = Callable[[str, str, list[dict]], tuple[str, dict]]


class EvidencePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str
    supports: str = "Supports the research result."


class DecisionMakerPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision_makers: list[dict] = Field(default_factory=list)
    employee_count: dict | None = None
    sources: list[EvidencePayload] = Field(default_factory=list)


class ContactPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = ""
    organization: str = ""
    linkedin: str = ""
    email: str = ""
    phone: str = ""
    sources: list[EvidencePayload | str] = Field(default_factory=list)


DECISION_PROMPT = """Search the web for up to three current decision makers for this Arizona commercial-property organization.
Organization ID: {organization_id}
Organization: {name}
Location: {location}
Date: {today}

Prefer local or regional authority over facilities, property/asset management, development, leasing, ownership, or operations. Verify the current role and never guess. Return strict JSON:
{{"decision_makers":[{{"name":"","title":"","scope":""}}],"employee_count":{{"value":"","scope":"company|location","as_of":"","confidence":"high|medium|low"}},"sources":[{{"url":"","supports":""}}]}}
Use an empty decision_makers list and null employee_count when nothing is verified."""


CONTACT_PROMPT = """Use web search to research this exact person and organization.
Person ID: {person_id}
Name: {name}
Organization: {organization}
Location: {location}

Find sourced professional LinkedIn, email, and phone details. Verify the identity and organization; never guess. Return strict JSON:
{{"name":"{name}","organization":"{organization}","linkedin":"","email":"","phone":"","sources":[{{"url":"","supports":""}}]}}
Use empty strings when a field cannot be verified."""


class DecisionMakerService:
    def __init__(
        self,
        state: StateStore,
        artifacts: ArtifactStore,
        model: str,
        call_model: ModelCall | None = None,
    ):
        self.state = state
        self.artifacts = artifacts
        self.model = model
        self.call_model = call_model or _default_model_call

    def research(self, organizations: list[Organization], attempts: int = 2) -> tuple[list[Person], list[ReviewItem]]:
        people: dict[str, Person] = {person.person_id: person for person in self.state.people()}
        reviews: list[ReviewItem] = []
        for organization in organizations:
            if any(person.organization_id == organization.organization_id for person in people.values()):
                continue
            last_review = None
            for attempt in range(1, attempts + 1):
                found, last_review = self._research_one(organization, attempt)
                if found:
                    people.update({person.person_id: person for person in found})
                    last_review = None
                    break
            if last_review:
                reviews.append(last_review)
        return list(people.values()), reviews

    def _research_one(self, organization: Organization, attempt_number: int) -> tuple[list[Person], ReviewItem | None]:
        attempt_id = stable_uuid(
            "attempt", organization.organization_id, "decision-maker", attempt_number
        )
        prompt = DECISION_PROMPT.format(
            organization_id=organization.organization_id,
            name=organization.canonical_name,
            location=organization.location,
            today=date.today().isoformat(),
        )
        request = self.artifacts.write_raw(
            "decision-makers", f"{attempt_id}-request.json", {"model": self.model, "prompt": prompt}
        )
        started = datetime.now(timezone.utc).isoformat()
        try:
            text, usage = self.call_model(self.model, prompt, [{"type": "web_search"}])
            response = self.artifacts.write_raw_text(
                "decision-makers", f"{attempt_id}-response.txt", text
            )
            payload = DecisionMakerPayload.model_validate(_parse_json(text))
            evidence = _evidence(payload.sources, "Supports the decision-maker research result.")
            if payload.employee_count is not None:
                self.state.save_organization(
                    organization.model_copy(update={"employee_count": payload.employee_count})
                )
            people: list[Person] = []
            for raw in payload.decision_makers[:3]:
                name = str(raw.get("name") or "").strip()
                if not name or not evidence:
                    continue
                person = Person(
                    person_id=person_id(name, organization.organization_id),
                    organization_id=organization.organization_id,
                    name=name,
                    title=str(raw.get("title") or "").strip(),
                    scope=str(raw.get("scope") or "").strip(),
                    evidence=evidence,
                )
                self.state.save_person(person)
                people.append(person)
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.artifacts.run_id,
                stage="decision-makers",
                provider="model",
                target_type="organization",
                target_id=organization.organization_id,
                status="completed",
                token_usage=usage,
                request_artifact_path=request["path"],
                response_artifact_path=response["path"],
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return people, None
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            review = ReviewItem(
                review_id=stable_uuid(
                    "review", self.artifacts.run_id, "decision-makers", organization.organization_id
                ),
                run_id=self.artifacts.run_id,
                stage="decision-makers",
                record_type="organization",
                record_id=organization.organization_id,
                reason_code="model_contract_invalid",
                validation_errors=[f"{type(exc).__name__}:{exc}"],
                raw_artifact_path=locals().get("response", {}).get("path", ""),
                retry_count=attempt_number,
            )
            self.state.add_review(review)
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.artifacts.run_id,
                stage="decision-makers",
                provider="model",
                target_type="organization",
                target_id=organization.organization_id,
                status="review",
                request_artifact_path=request["path"],
                response_artifact_path=review.raw_artifact_path,
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return [], review


class ContactResearchService:
    def __init__(
        self,
        state: StateStore,
        artifacts: ArtifactStore,
        model: str,
        verifier: ContactVerifier,
        call_model: ModelCall | None = None,
    ):
        self.state = state
        self.artifacts = artifacts
        self.model = model
        self.verifier = verifier
        self.call_model = call_model or _default_model_call

    def research(
        self,
        people: list[Person],
        organizations: list[Organization],
        events: list[LeadEvent],
        attempts: int = 2,
    ) -> tuple[list[ContactCandidate], list[ReviewItem]]:
        org_by_id = {item.organization_id: item for item in organizations}
        events_by_org: dict[str, list[LeadEvent]] = {}
        for event in events:
            events_by_org.setdefault(event.organization_id, []).append(event)
        candidates: list[ContactCandidate] = []
        reviews: list[ReviewItem] = []
        for person in people:
            organization = org_by_id.get(person.organization_id)
            if not organization:
                continue
            payload = None
            last_review = None
            for attempt in range(1, attempts + 1):
                payload, last_review = self._research_one(person, organization, attempt)
                if payload is not None and any(
                    (payload.email, payload.phone, payload.linkedin)
                ):
                    last_review = None
                    break
                payload = None
            if last_review:
                reviews.append(last_review)
            if payload is None:
                continue
            evidence = _evidence(payload.sources, f"Supports contact details for {person.name}.")
            if not evidence or not any((payload.email, payload.phone, payload.linkedin)):
                continue
            verification = self.verifier.verify(
                email=payload.email,
                phone=payload.phone,
                linkedin=payload.linkedin,
                organization_domain=organization.domain,
            )
            for event in events_by_org.get(person.organization_id, []):
                contact = ContactCandidate(
                    contact_candidate_id=stable_uuid(
                        "contact-candidate",
                        event.lead_event_id,
                        person.person_id,
                        "model",
                        stable_hash(payload.email, payload.phone, payload.linkedin),
                    ),
                    run_id=event.run_id,
                    lead_event_id=event.lead_event_id,
                    organization_id=person.organization_id,
                    person_id=person.person_id,
                    person_name=person.name,
                    title=person.title,
                    email=verification.email,
                    phone=verification.phone,
                    linkedin=verification.linkedin,
                    provider="model",
                    verification_status=verification.status,
                    verification_reason=verification.reason,
                    evidence=evidence,
                )
                candidates.append(contact)
        selected = select_best(candidates)
        for contact in selected:
            self.state.save_contact(contact)
        return selected, reviews

    def _research_one(
        self, person: Person, organization: Organization, attempt_number: int
    ) -> tuple[ContactPayload | None, ReviewItem | None]:
        attempt_id = stable_uuid("attempt", person.person_id, "contact", attempt_number)
        prompt = CONTACT_PROMPT.format(
            person_id=person.person_id,
            name=person.name,
            organization=organization.canonical_name,
            location=organization.location,
        )
        request = self.artifacts.write_raw(
            "contacts", f"{attempt_id}-request.json", {"model": self.model, "prompt": prompt}
        )
        started = datetime.now(timezone.utc).isoformat()
        try:
            text, usage = self.call_model(self.model, prompt, [{"type": "web_search"}])
            response = self.artifacts.write_raw_text("contacts", f"{attempt_id}-response.txt", text)
            payload = ContactPayload.model_validate(_parse_json(text))
            if payload.name and normalize_text(payload.name) != normalize_text(person.name):
                raise ValueError("returned contact name does not match target person")
            if payload.organization:
                expected = normalize_text(organization.canonical_name)
                returned = normalize_text(payload.organization)
                if expected not in returned and returned not in expected:
                    raise ValueError("returned contact organization does not match target")
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.artifacts.run_id,
                stage="contacts",
                provider="model",
                target_type="person",
                target_id=person.person_id,
                status="completed",
                token_usage=usage,
                request_artifact_path=request["path"],
                response_artifact_path=response["path"],
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return payload, None
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            review = ReviewItem(
                review_id=stable_uuid("review", self.artifacts.run_id, "contacts", person.person_id),
                run_id=self.artifacts.run_id,
                stage="contacts",
                record_type="person",
                record_id=person.person_id,
                reason_code="model_contract_invalid",
                validation_errors=[f"{type(exc).__name__}:{exc}"],
                raw_artifact_path=locals().get("response", {}).get("path", ""),
                retry_count=attempt_number,
            )
            self.state.add_review(review)
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.artifacts.run_id,
                stage="contacts",
                provider="model",
                target_type="person",
                target_id=person.person_id,
                status="review",
                request_artifact_path=request["path"],
                response_artifact_path=review.raw_artifact_path,
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return None, review


def _evidence(values: list[EvidencePayload | str], default_supports: str) -> list[Evidence]:
    out: list[Evidence] = []
    for value in values:
        if isinstance(value, str):
            url, supports = value, default_supports
        else:
            url, supports = value.url, value.supports or default_supports
        try:
            out.append(Evidence(url=url, supports=supports, provider="web"))
        except ValidationError:
            continue
    return out


def _parse_json(text: str) -> object:
    cleaned = re.sub(r"<<ccr:[^>]+>>", "", str(text or ""))
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("model response did not contain a JSON object")
    return json.loads(match.group())


def _default_model_call(model: str, prompt: str, tools: list[dict]) -> tuple[str, dict]:
    import llm

    return llm.call(
        model,
        prompt,
        tools=tools,
        text_format="json_object",
        with_usage=True,
    )
