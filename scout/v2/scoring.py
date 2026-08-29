"""Completeness-checked lead-event scoring that never drops a lead."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable

from .artifacts import ArtifactStore
from .contracts import ContactCandidate, LeadEvent, LeadScore, ReviewItem
from .ids import stable_uuid
from .state import StateStore


ModelCall = Callable[[str, str, list[dict]], tuple[str, dict]]


PROMPT = """Score each Arizona commercial-property lead event from 0 to 100 for Aether Facility Services outreach priority. Consider activity fit, property fit, timing, location, and reachable verified contacts. A zero is an explicit valid score, not missing data.

Input events:
{events}

Return strict JSON only: an object mapping every exact lead_event_id to one integer 0-100. Include every submitted ID exactly once and invent no IDs."""


class ScoreContractError(ValueError):
    pass


class ScoringService:
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

    def score(
        self,
        events: list[LeadEvent],
        contacts: list[ContactCandidate],
        attempts: int = 2,
    ) -> tuple[list[LeadScore], list[ReviewItem]]:
        if not events:
            return [], []
        contacts_by_event: dict[str, list[dict]] = {}
        for contact in contacts:
            if not contact.selected:
                continue
            contacts_by_event.setdefault(contact.lead_event_id, []).append(
                {
                    "person_id": contact.person_id,
                    "email": bool(contact.email),
                    "phone": bool(contact.phone),
                    "verification_status": contact.verification_status.value,
                }
            )
        inputs = [
            {
                "lead_event_id": event.lead_event_id,
                "event": event.event,
                "location": event.location,
                "date_posted": str(event.date_posted or ""),
                "summary": event.summary,
                "priority": event.priority,
                "property_type": event.property_type,
                "contacts": contacts_by_event.get(event.lead_event_id, []),
            }
            for event in events
        ]
        prompt = PROMPT.format(events=json.dumps(inputs, sort_keys=True))
        last_error: Exception | None = None
        for attempt_number in range(1, attempts + 1):
            attempt_id = stable_uuid(
                "attempt", self.artifacts.run_id, "score", attempt_number
            )
            request = self.artifacts.write_raw(
                "score", f"{attempt_id}-request.json", {"model": self.model, "prompt": prompt}
            )
            started = datetime.now(timezone.utc).isoformat()
            try:
                text, usage = self.call_model(self.model, prompt, [])
                response = self.artifacts.write_raw_text(
                    "score", f"{attempt_id}-response.txt", text
                )
                parsed = parse_scores(text, {event.lead_event_id for event in events})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                last_error = exc
                self.state.record_provider_attempt(
                    attempt_id=attempt_id,
                    run_id=self.artifacts.run_id,
                    stage="score",
                    provider="model",
                    target_type="lead_event_batch",
                    target_id=self.artifacts.run_id,
                    status="review",
                    request_artifact_path=request["path"],
                    response_artifact_path=locals().get("response", {}).get("path", ""),
                    error={"type": type(exc).__name__, "message": str(exc)},
                    started_at=started,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                continue
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.artifacts.run_id,
                stage="score",
                provider="model",
                target_type="lead_event_batch",
                target_id=self.artifacts.run_id,
                status="completed",
                token_usage=usage,
                request_artifact_path=request["path"],
                response_artifact_path=response["path"],
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            scores = [
                LeadScore(
                    run_id=self.artifacts.run_id,
                    lead_event_id=event.lead_event_id,
                    score=parsed[event.lead_event_id],
                    model=self.model,
                    attempt_id=attempt_id,
                )
                for event in events
            ]
            for score in scores:
                self.state.save_score(score)
            return scores, []
        error_text = f"{type(last_error).__name__}:{last_error}"
        reviews = [
            ReviewItem(
                review_id=stable_uuid("review", self.artifacts.run_id, "score", event.lead_event_id),
                run_id=self.artifacts.run_id,
                stage="score",
                record_type="lead_event",
                record_id=event.lead_event_id,
                reason_code="score_batch_incomplete",
                validation_errors=[error_text],
                retry_count=attempts,
            )
            for event in events
        ]
        for review in reviews:
            self.state.add_review(review)
        return [], reviews


def parse_scores(text: str, expected_ids: set[str]) -> dict[str, int]:
    cleaned = re.sub(r"<<ccr:[^>]+>>", "", str(text or ""))
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ScoreContractError("score response did not contain a JSON object")
    payload = json.loads(match.group())
    if not isinstance(payload, dict):
        raise ScoreContractError("score response must be an object")
    actual = set(payload)
    if actual != expected_ids:
        raise ScoreContractError(
            f"score IDs must match exactly; missing={sorted(expected_ids - actual)}, unknown={sorted(actual - expected_ids)}"
        )
    scores: dict[str, int] = {}
    for event_id, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ScoreContractError(f"score for {event_id} must be an integer")
        score = int(value)
        if not 0 <= score <= 100:
            raise ScoreContractError(f"score for {event_id} is outside 0-100")
        scores[event_id] = score
    return scores


def _default_model_call(model: str, prompt: str, tools: list[dict]) -> tuple[str, dict]:
    import llm

    return llm.call(model, prompt, tools=tools, text_format="json_object", with_usage=True)
