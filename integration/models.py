"""Shared contracts for the integration boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class VerificationStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"
    CATCH_ALL = "catch_all"
    UNKNOWN = "unknown"


class ReplyDisposition(StrEnum):
    PENDING_REVIEW = "pending_review"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    OUT_OF_OFFICE = "out_of_office"
    UNSUBSCRIBE = "unsubscribe"
    OTHER = "other"


class SuppressionReason(StrEnum):
    BOUNCE = "bounce"
    UNSUBSCRIBE = "unsubscribe"
    NEGATIVE_REPLY = "negative_reply"
    INVALID = "invalid"
    MANUAL = "manual"
    COMPLAINT = "complaint"


class ContactSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    lead_event_id: str
    organization_id: str
    person_id: str
    outreach_id: str
    source_contact_candidate_id: str = ""
    source_verification_status: str = "unknown"
    source_verification_reason: str = ""
    source_provider: str = ""
    organization_name: str = Field(min_length=1)
    person_name: str = Field(min_length=1)
    email: str
    title: str = ""
    phone: str = ""
    linkedin: str = ""
    location: str = ""
    event: str = ""
    article_url: str = ""
    date_posted: str = ""
    summary: str = ""
    is_primary: bool = False
    source_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized or "@" not in normalized:
            raise ValueError("contact requires an email address")
        return normalized


class WarmyEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    data: dict[str, Any]
    timestamp: datetime | None = None


class WorkItem(BaseModel):
    id: int
    kind: str
    dedupe_key: str
    payload: dict[str, Any]
    attempt_count: int = 0


class MappingRecord(BaseModel):
    outreach_id: str
    source_contact_candidate_id: str = ""
    source_verification_status: str = "unknown"
    source_verification_reason: str = ""
    source_provider: str = ""
    email: str
    lead_event_id: str = ""
    organization_id: str = ""
    person_id: str = ""
    pipedrive_organization_id: int | None = None
    pipedrive_person_id: int | None = None
    pipedrive_lead_id: str | None = None
    pipedrive_deal_id: int | None = None
    warmy_prospect_id: str | None = None
    warmy_campaign_id: str | None = None
    warmy_mailbox_id: str | None = None
    gmail_thread_id: str | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    reply_disposition: ReplyDisposition | None = None
    reply_received_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
