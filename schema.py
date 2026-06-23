from datetime import date
from typing import Literal

from pydantic import BaseModel, model_validator


class ExtractedArticle(BaseModel):
    title: str
    published_date: date | None
    summary_2sent: str
    signal_type: Literal[
        "opening", "development", "acquisition",
        "expansion", "lease", "construction", "other",
    ]
    company_name: str
    company_domain_guess: str | None
    property_type: Literal[
        "office", "industrial", "multifamily",
        "retail", "medical", "mixed", "other",
    ]
    address: str | None
    city: str | None
    square_footage: int | None
    dollar_value: int | None
    unit_count: int | None
    az_relevant: bool
    confidence: float
    # Jordan's protocol (see AGENTS.md Step 2b for the rules):
    #  high   — active lease-up, new occupancy, openings, expansions, mgmt changes
    #  medium — land acquisitions, industrial deals, generic commercial transactions
    #  low    — macro commentary, mortgage news, residential, rankings/awards,
    #           out-of-state, anything dropped by is_qualifying
    priority: Literal["high", "medium", "low"]
    # One sentence explaining the priority — populated for ALL articles (incl. low/dropped).
    # Flows into the Pipedrive note + downstream audit trail.
    filter_reason: str
    # Aether-voice reason to reach out: asset-preservation framing, not "cleaning".
    # Null on low-priority (those are dropped before push); required for high/medium.
    service_angle: str | None

    @model_validator(mode="after")
    def _check_required_text_fields(self) -> "ExtractedArticle":
        # filter_reason is the audit trail — blank means we silently lose the
        # "why" for this article across all downstream consumers.
        if not self.filter_reason.strip():
            raise ValueError("filter_reason must be non-empty (audit trail)")

        # service_angle drives the Pipedrive note's "Aether angle" line.
        # Anything that reaches push (priority in {high, medium}) must have one.
        # Low-priority articles can omit it since they're dropped before push.
        if self.priority in ("high", "medium"):
            if self.service_angle is None or not self.service_angle.strip():
                raise ValueError(
                    f"priority={self.priority!r} requires a non-empty service_angle"
                )
        return self
