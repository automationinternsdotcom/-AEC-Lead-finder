"""Typed article qualification and completeness-safe model handling."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Callable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .artifacts import ArtifactStore
from .contracts import (
    DiscoveryCandidate,
    Evidence,
    LeadEvent,
    Organization,
    Person,
    RecordStatus,
    ReviewItem,
)
from .ids import event_id, organization_id, person_id, stable_hash, stable_uuid
from .state import StateStore


ModelCall = Callable[[str, str, list[dict]], tuple[str, dict]]


QUALIFICATION_PROMPT = """Open and read the exact article URL below using web search. Determine whether it reports a specific Arizona commercial-property event that could create a facilities-services opportunity. Never infer rejection from missing data.

Candidate ID: {candidate_id}
URL: {url}
Title: {title}

Return strict JSON with these keys:
qualified, business_name, person, event, date_posted, location, summary, state,
priority, property_type, service_angle, filter_reason, confidence.

For an explicit non-qualifying article, return qualified=false and a nonempty filter_reason. For a qualifying article, state must be Arizona, priority must be high or medium, confidence must be high or low, and business_name, event, and location must be nonempty. Use empty strings for unknown optional values. Return no prose."""


class JudgmentPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    qualified: bool
    business_name: str = ""
    person: str = ""
    event: str = ""
    date_posted: date | None = None
    location: str = ""
    summary: str = ""
    state: str = ""
    priority: str = ""
    property_type: str = "other"
    service_angle: str = ""
    filter_reason: str = ""
    confidence: str = "high"

    @field_validator("date_posted", mode="before")
    @classmethod
    def blank_date_is_unknown(cls, value):
        return None if value == "" else value

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            threshold = 0.5 if 0 <= value <= 1 else 50
            return "high" if value >= threshold else "low"
        return value

    @model_validator(mode="after")
    def complete_decision(self) -> "JudgmentPayload":
        if not self.qualified:
            if not self.filter_reason.strip():
                raise ValueError("explicit rejections require filter_reason")
            return self
        missing = [
            field
            for field in ("business_name", "event", "location", "state", "priority")
            if not str(getattr(self, field) or "").strip()
        ]
        if missing:
            raise ValueError(f"qualified result missing fields: {', '.join(missing)}")
        if self.state != "Arizona":
            raise ValueError("qualified result must be in Arizona")
        if self.priority.casefold() not in {"high", "medium"}:
            raise ValueError("qualified result priority must be high or medium")
        if self.confidence not in {"high", "low"}:
            raise ValueError("confidence must be high or low")
        return self


class QualificationResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    events: list[LeadEvent] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)
    reviews: list[ReviewItem] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)


class QualificationService:
    def __init__(
        self,
        state: StateStore,
        artifacts: ArtifactStore,
        model: str,
        call_model: ModelCall | None = None,
        workers: int = 1,
    ):
        self.state = state
        self.artifacts = artifacts
        self.model = model
        self.call_model = call_model or _default_model_call
        self.workers = max(1, workers)

    def qualify(
        self, candidates: list[DiscoveryCandidate], *, retry_review: bool = False
    ) -> QualificationResult:
        result = QualificationResult()
        eligible = []
        for candidate in candidates:
            if candidate.record_status != RecordStatus.VALID and not (
                retry_review and candidate.record_status == RecordStatus.REVIEW
            ):
                continue
            if retry_review and candidate.record_status == RecordStatus.REVIEW:
                candidate = candidate.model_copy(
                    update={"record_status": RecordStatus.VALID, "validation_errors": []}
                )
            eligible.append(candidate)
        if self.workers == 1:
            outcomes = map(self._qualify_one, eligible)
        else:
            pool = ThreadPoolExecutor(max_workers=self.workers)
            outcomes = pool.map(self._qualify_one, eligible)
        try:
            for event, person, review, rejected in outcomes:
                if event:
                    result.events.append(event)
                if person:
                    result.people.append(person)
                if review:
                    result.reviews.append(review)
                if rejected:
                    result.rejected_candidate_ids.append(rejected)
        finally:
            if self.workers != 1:
                pool.shutdown(wait=True, cancel_futures=True)
        return result

    def _qualify_one(
        self, candidate: DiscoveryCandidate
    ) -> tuple[LeadEvent | None, Person | None, ReviewItem | None, str]:
        attempt_id = stable_uuid("attempt", candidate.run_id, "qualify", candidate.candidate_id)
        prompt = QUALIFICATION_PROMPT.format(
            candidate_id=candidate.candidate_id,
            url=candidate.canonical_url,
            title=candidate.title,
        )
        request = self.artifacts.write_raw(
            "qualify", f"{attempt_id}-request.json", {"model": self.model, "prompt": prompt}
        )
        started = datetime.now(timezone.utc).isoformat()
        try:
            text, usage = self.call_model(self.model, prompt, [{"type": "web_search"}])
            response = self.artifacts.write_raw_text(
                "qualify", f"{attempt_id}-response.txt", text
            )
            payload = JudgmentPayload.model_validate(_parse_json(text))
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._quarantine(
                candidate,
                attempt_id,
                request["path"],
                locals().get("response", {}).get("path", ""),
                started,
                exc,
            )
        except Exception as exc:
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=candidate.run_id,
                stage="qualify",
                provider="model",
                target_type="discovery_candidate",
                target_id=candidate.candidate_id,
                status="failed",
                request_artifact_path=request["path"],
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            raise
        self.state.record_provider_attempt(
            attempt_id=attempt_id,
            run_id=candidate.run_id,
            stage="qualify",
            provider="model",
            target_type="discovery_candidate",
            target_id=candidate.candidate_id,
            status="completed",
            token_usage=usage,
            request_artifact_path=request["path"],
            response_artifact_path=response["path"],
            started_at=started,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if not payload.qualified:
            rejected = candidate.model_copy(update={"record_status": RecordStatus.REJECTED})
            self.state.save_candidate(rejected)
            return None, None, None, candidate.candidate_id

        org_id = organization_id(payload.business_name, "", payload.location)
        support_ids = candidate.metadata.get("exact_duplicate_candidate_ids") or [
            candidate.candidate_id
        ]
        support_candidates = {
            item.candidate_id: item
            for item in self.state.candidates_for_run(candidate.run_id)
            if item.candidate_id in support_ids
        }
        evidence = [
            Evidence(
                url=support_candidates[item].canonical_url,
                supports="Source article for the qualified property event.",
                provider=support_candidates[item].provider,
            )
            for item in support_ids
            if item in support_candidates
        ] or [
            Evidence(
                url=candidate.canonical_url,
                supports="Source article for the qualified property event.",
                provider=candidate.provider,
            )
        ]
        organization = Organization(
            organization_id=org_id,
            canonical_name=payload.business_name.strip(),
            location=payload.location.strip(),
            evidence=evidence,
        )
        self.state.save_organization(organization)
        lead_event_id = event_id(
            org_id,
            payload.event,
            payload.location,
            (payload.date_posted or (candidate.published_at.date() if candidate.published_at else "")),
        )
        event = LeadEvent(
            lead_event_id=lead_event_id,
            run_id=candidate.run_id,
            organization_id=org_id,
            primary_candidate_id=candidate.candidate_id,
            supporting_candidate_ids=list(support_ids),
            event=payload.event.strip(),
            location=payload.location.strip(),
            date_posted=payload.date_posted or (
                candidate.published_at.date() if candidate.published_at else None
            ),
            summary=payload.summary.strip(),
            priority=payload.priority,
            property_type=payload.property_type.strip() or "other",
            service_angle=payload.service_angle.strip(),
            filter_reason=payload.filter_reason.strip(),
            confidence=payload.confidence,
            evidence=evidence,
        )
        self.state.save_lead_event(event)
        person = None
        if payload.person.strip():
            pid = person_id(payload.person, org_id)
            person = Person(
                person_id=pid,
                organization_id=org_id,
                name=payload.person.strip(),
                evidence=evidence,
            )
            self.state.save_person(person)
        return event, person, None, ""

    def _quarantine(
        self,
        candidate: DiscoveryCandidate,
        attempt_id: str,
        request_path: str,
        response_path: str,
        started: str,
        exc: Exception,
    ) -> tuple[None, None, ReviewItem, str]:
        error = f"{type(exc).__name__}:{exc}"
        updated = candidate.model_copy(
            update={
                "record_status": RecordStatus.REVIEW,
                "validation_errors": [*candidate.validation_errors, error],
            }
        )
        self.state.save_candidate(updated)
        review = ReviewItem(
            review_id=stable_uuid("review", candidate.run_id, "qualify", candidate.candidate_id),
            run_id=candidate.run_id,
            stage="qualify",
            record_type="discovery_candidate",
            record_id=candidate.candidate_id,
            reason_code="model_contract_invalid",
            validation_errors=[error],
            raw_artifact_path=response_path,
        )
        self.state.add_review(review)
        self.state.record_provider_attempt(
            attempt_id=attempt_id,
            run_id=candidate.run_id,
            stage="qualify",
            provider="model",
            target_type="discovery_candidate",
            target_id=candidate.candidate_id,
            status="review",
            request_artifact_path=request_path,
            response_artifact_path=response_path,
            error={"type": type(exc).__name__, "message": str(exc)},
            started_at=started,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return None, None, review, ""


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
