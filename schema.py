from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


PhoenixMetroCity = Literal[
    "Phoenix",
    "Scottsdale",
    "Mesa",
    "Tempe",
    "Chandler",
    "Gilbert",
    "Glendale",
    "Peoria",
    "Surprise",
    "Goodyear",
]

PropertyType = Literal["office", "retail", "medical", "industrial"]
SourceType = Literal["rss", "website", "hybrid", "disabled"]
SourceStatus = Literal["active", "paused", "failed", "disabled"]
QualificationStatus = Literal["qualified", "review", "rejected"]
EnrichmentStatus = Literal["not_needed", "pending", "complete", "failed", "review"]
PipedriveStatus = Literal["not_ready", "pending", "created", "updated", "duplicate", "failed"]
ReviewStatus = Literal["new", "approved", "rejected", "re_enrich", "merge", "hold"]
LeadRoute = Literal["review", "store_only", "reject"]
RecencyStatus = Literal[
    "within_14_days_opened",
    "within_90_days_opening",
    "announced",
    "topped_out",
    "leased_up",
    "too_far_out",
    "unknown",
]


class SourceRecord(BaseModel):
    source_id: str
    site_name: str
    source_type: SourceType
    source_url: str
    rss_url: str | None = None
    crawl_start_url: str | None = None
    allowed_paths: list[str] = Field(default_factory=list)
    city_scope: str | None = None
    status: SourceStatus = "active"
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    notes: str | None = None

    @field_validator("allowed_paths", mode="before")
    @classmethod
    def parse_allowed_paths(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(",") if item.strip()]

    @field_validator("rss_url", "crawl_start_url", "city_scope", "last_error", "notes", mode="before")
    @classmethod
    def blank_strings_are_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("last_checked_at", "last_success_at", mode="before")
    @classmethod
    def blank_datetimes_are_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("status", mode="before")
    @classmethod
    def blank_status_is_active(cls, value: Any) -> Any:
        return "active" if value == "" or value is None else value

    @property
    def is_active(self) -> bool:
        return self.source_type != "disabled" and self.status == "active"


class FetchedPage(BaseModel):
    source_id: str
    source_site: str
    source_type: SourceType
    url: str
    title: str | None = None
    published_at: datetime | None = None
    first_seen_at: datetime = Field(default_factory=utc_now)
    raw_html: str | None = None
    cleaned_text: str | None = None


class Evidence(BaseModel):
    field: str
    quote: str


class Qualification(BaseModel):
    is_qualified: bool
    city: str | None = None
    reason: str
    recency_status: RecencyStatus = "unknown"


class ExtractionResult(BaseModel):
    property_name: str | None = None
    address: str | None = None
    property_type: PropertyType | None = None
    opening_date_or_status: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    description: str | None = None
    source_url: str
    qualification: Qualification
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def has_article_contact(self) -> bool:
        return bool(self.contact_name or self.contact_email or self.contact_phone)


class BestContact(BaseModel):
    name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    why_this_person: str | None = None

    @property
    def has_contact(self) -> bool:
        return bool(self.name or self.email or self.phone)


class EnrichmentEvidence(BaseModel):
    field: str
    source_url: str
    source_title: str | None = None
    reason: str


class EnrichmentResult(BaseModel):
    owner_company: str | None = None
    developer_company: str | None = None
    property_manager_company: str | None = None
    tenant_or_operator: str | None = None
    best_contact: BestContact = Field(default_factory=BestContact)
    evidence: list[EnrichmentEvidence] = Field(default_factory=list)
    enrichment_confidence: float = Field(default=0, ge=0, le=1)
    needs_human_review: bool = True


class ScoreLine(BaseModel):
    signal: str
    points: int
    reason: str


class ScoreBreakdown(BaseModel):
    score: int
    route: LeadRoute
    lines: list[ScoreLine] = Field(default_factory=list)


class LeadRecord(BaseModel):
    lead_id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    source_site: str
    source_url: str
    article_title: str | None = None
    published_at: datetime | None = None
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    property_name: str | None = None
    address: str | None = None
    property_type: PropertyType | None = None
    opening_date_or_status: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    description: str | None = None
    qualification_status: QualificationStatus
    qualification_reason: str
    confidence_score: int = 0
    enrichment_status: EnrichmentStatus = "pending"
    pipedrive_status: PipedriveStatus = "not_ready"
    pipedrive_org_id: str | None = None
    pipedrive_person_id: str | None = None
    pipedrive_lead_id: str | None = None
    dedupe_key: str | None = None
    rejection_reason: str | None = None
    extraction: ExtractionResult | None = None
    enrichment: EnrichmentResult | None = None
    score_breakdown: ScoreBreakdown | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ReviewDecision(BaseModel):
    lead_id: str
    review_status: ReviewStatus
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    reason: str | None = None
    next_action: str | None = None


class DedupeResult(BaseModel):
    outcome: Literal["exact_duplicate", "probable_duplicate", "existing_org_new_lead", "new"]
    reason: str
    existing_lead_id: str | None = None
    existing_org_id: str | None = None


class PipedriveSyncResult(BaseModel):
    status: PipedriveStatus
    org_id: str | None = None
    person_id: str | None = None
    lead_id: str | None = None
    message: str | None = None
