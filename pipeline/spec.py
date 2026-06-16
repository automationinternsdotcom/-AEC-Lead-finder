"""Campaign spec — the single config object that defines a client's targeting.

This is Layer 2 of the product plan (the "vertical skin"). The engine reads a
CampaignSpec and runs it; the spec is the only thing that changes per client.
A new vertical is a new spec file (hours), not new code (weeks).

Nothing here changes pipeline behavior yet — Phase 0 is purely the typed schema
plus its loader. Later phases wire fetch/assessor/enrich/push to read from a
loaded CampaignSpec instead of today's hardcoded CRE/AZ logic.

Reuses: pydantic (matches schema.py's ExtractedArticle style), pyyaml + the
ROOT path convention from config.py.
Extend: add a field to the relevant sub-model; specs validate against it on load.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from pipeline.config import ROOT

CAMPAIGNS_DIR = ROOT / "campaigns"


class Client(BaseModel):
    """Who we're finding leads for. Feeds the planner and every enrich prompt."""
    name: str
    company_description: str
    # Free-form regions ("Arizona", "Phoenix metro", "US"). The engine uses
    # these for context; `discovery.geography` holds the machine-filterable codes.
    service_area: list[str]


class Targeting(BaseModel):
    """The 'what is a lead' definition, in config rather than a hardcoded prompt."""
    industry: str
    # Publicly-visible events that mark a buying moment (new construction,
    # funding round, lease-up). The CRE vertical's signals live in the daily
    # routine's HIGH/MEDIUM protocol today; this externalizes them.
    trigger_signals: list[str]
    buyer_personas: list[str]
    # Hard-exclude terms (residential, DIY, consumer) — the cheap pre-filter and
    # the relevance stage both read these.
    negative_keywords: list[str] = Field(default_factory=list)


class Discovery(BaseModel):
    """How the engine finds candidates: search queries + tagged feeds + geo."""
    search_queries: list[str]
    # Tags into the (future) sources registry — the planner selects feeds by tag
    # rather than hardcoding URLs. Today's sources.yaml is the seed for this.
    source_tags: list[str] = Field(default_factory=list)
    # Machine-filterable region codes (e.g. ["AZ"]). Distinct from the free-form
    # client.service_area above.
    geography: list[str] = Field(default_factory=list)


class Qualification(BaseModel):
    """The keep/drop gate applied to extracted candidates."""
    # One paragraph the assess stage applies verbatim. For CRE this is Jordan's
    # HIGH/MEDIUM/LOW protocol distilled to a rubric.
    relevance_rubric: str
    min_confidence: float = Field(ge=0.0, le=1.0)


class Enrichment(BaseModel):
    """Context handed to the contact-enrichment stage (Layer 1, stage 5)."""
    buyer_persona: str
    # The reason-to-reach-out framing. For CRE this is the "asset preservation /
    # strategic partner" voice — deliberately NOT "cleaning".
    outreach_angle: str
    # Fields a lead must carry to be considered enriched.
    required_fields: list[str] = Field(default_factory=list)


class PipedriveConfig(BaseModel):
    """Pipedrive destination settings. Never store the raw token here — only a
    reference into a secrets store (plan §6.1)."""
    api_key_ref: str
    # MVP maps to standard Pipedrive objects/fields only; every account has these.
    field_mapping: Literal["standard"] = "standard"


class Destination(BaseModel):
    """Where qualified leads land (Layer 3). Excel is the always-works fallback."""
    type: Literal["pipedrive", "excel"]
    pipedrive: PipedriveConfig | None = None
    fallback: Literal["excel"] = "excel"

    @model_validator(mode="after")
    def _pipedrive_requires_config(self) -> "Destination":
        # A pipedrive destination with no pipedrive block can't deliver — fail at
        # load time rather than silently at the first push.
        if self.type == "pipedrive" and self.pipedrive is None:
            raise ValueError("destination.type='pipedrive' requires a 'pipedrive' block")
        return self


class Schedule(BaseModel):
    """When the campaign runs and the human-in-the-loop guardrails (plan §12)."""
    cadence: Literal["nightly", "weekly", "manual"] = "nightly"
    # Keep the preview/confirm step before any live CRM write — non-negotiable.
    preview_before_push: bool = True
    # Small first batch so a misconfigured campaign can't flood a client's CRM.
    max_first_push: int = Field(default=2, ge=0)


class CampaignSpec(BaseModel):
    """The core object. Everything in the engine reads from this."""
    campaign_id: str = Field(min_length=1)
    client: Client
    targeting: Targeting
    discovery: Discovery
    qualification: Qualification
    enrichment: Enrichment
    destination: Destination
    schedule: Schedule = Field(default_factory=Schedule)


def load_spec(path: str | Path) -> CampaignSpec:
    """Load + validate a campaign spec from a YAML file.

    Raises pydantic.ValidationError on a malformed spec — callers should let it
    surface rather than ship a half-defined campaign.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CampaignSpec.model_validate(data)
