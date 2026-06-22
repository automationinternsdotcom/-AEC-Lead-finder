"""Campaign spec — the single config object that defines a client's targeting.

This is Layer 2 of the product plan (the "vertical skin"). The engine reads a
CampaignSpec and runs it; the spec is the only thing that changes per client.
A new vertical is a new spec file (hours), not new code (weeks).

Phase 1 wires the current cleaning pipeline to load this spec at runtime while
preserving today's behavior. New verticals should add specs, not new bespoke
pipeline branches.

Reuses: pydantic (matches schema.py's ExtractedArticle style), pyyaml + the
ROOT path convention from config.py.
Extend: add a field to the relevant sub-model; specs validate against it on load.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from pipeline.config import ROOT

CAMPAIGNS_DIR = ROOT / "campaigns"
DEFAULT_CAMPAIGN_ID = "aether-cleaning-az"


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


StageRoute = Literal[
    "deterministic_cli",
    "codex_in_session",
    "browser_chat_skill",
    "manual_review",
    "disabled",
]

LeadPatternType = Literal[
    "event_signal",
    "entity_aggregation",
    "multi_signal_intent",
    "relationship_graph",
    "competitive_displacement",
    "lifecycle_trigger_window",
    "compliance_regulation",
    "hybrid",
]


class SpecIdentity(BaseModel):
    """Stable campaign identity for run folders, reports, and destinations."""
    campaign_id: str = Field(min_length=1)
    name: str
    client_name: str
    vertical: str | None = None


class TargetProfileV2(BaseModel):
    """Normalized ICP block used by future non-CRE campaign specs."""
    industry: str
    geography: list[str] = Field(default_factory=list)
    service_area: list[str] = Field(default_factory=list)
    buyer_personas: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)


class LeadPattern(BaseModel):
    """Which lead-finding family this campaign uses."""
    type: LeadPatternType = "event_signal"
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class SignalConfig(BaseModel):
    """Buying-moment signals and explicit exclusions."""
    trigger_signals: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)


class SourceConfig(BaseModel):
    """Spec-selected source rows and generated search queries."""
    search_queries: list[str] = Field(default_factory=list)
    source_tags: list[str] = Field(default_factory=list)
    geography: list[str] = Field(default_factory=list)


class QualificationV2(BaseModel):
    """Resolved relevance rubric and minimum quality threshold."""
    relevance_rubric: str
    min_confidence: float = Field(ge=0.0, le=1.0)


class EnrichmentV2(BaseModel):
    """Contact/persona guidance for enrichment stages."""
    buyer_persona: str
    outreach_angle: str
    required_fields: list[str] = Field(default_factory=list)


class StageRouting(BaseModel):
    """Declares which actor owns each stage; no fake browser API in Python."""
    fetch: StageRoute = "deterministic_cli"
    extract: StageRoute = "deterministic_cli"
    pattern: StageRoute = "deterministic_cli"
    qualify: StageRoute = "codex_in_session"
    enrich: StageRoute = "browser_chat_skill"
    preview: StageRoute = "manual_review"
    deliver: StageRoute = "manual_review"


class DestinationV2(BaseModel):
    """Resolved destination entry. Secrets are references, never raw values."""
    type: Literal["pipedrive", "excel", "email", "webhook"]
    enabled: bool = True
    credential_ref: str | None = None
    field_mapping: Literal["standard"] = "standard"
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _live_destinations_need_credentials(self) -> "DestinationV2":
        if (
            self.enabled
            and self.type in {"pipedrive", "email", "webhook"}
            and not self.credential_ref
        ):
            raise ValueError(f"destination {self.type!r} requires credential_ref")
        return self


class RunPolicy(BaseModel):
    cadence: Literal["nightly", "weekly", "manual"] = "nightly"
    preview_before_push: bool = True
    max_first_push: int = Field(default=2, ge=0)


class QualityGates(BaseModel):
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    preserve_phase1_parity: bool = False
    require_human_preview: bool = True


class CampaignSpecV2(BaseModel):
    """Resolved Phase 2 campaign contract.

    This model is the general-engine shape. Phase 1 code still consumes
    CampaignSpec; Phase 2A resolves both old and new YAML into this object for
    run-state, validation, and future pattern modules.
    """
    schema_version: Literal["campaign_spec.v2"] = "campaign_spec.v2"
    identity: SpecIdentity
    target_profile: TargetProfileV2
    lead_pattern: LeadPattern = Field(default_factory=LeadPattern)
    signals: SignalConfig
    sources: SourceConfig
    qualification: QualificationV2
    enrichment: EnrichmentV2
    routing: StageRouting = Field(default_factory=StageRouting)
    destinations: list[DestinationV2]
    run_policy: RunPolicy = Field(default_factory=RunPolicy)
    quality_gates: QualityGates = Field(default_factory=QualityGates)

    @property
    def campaign_id(self) -> str:
        return self.identity.campaign_id


def campaign_spec_v1_to_v2(spec: CampaignSpec) -> CampaignSpecV2:
    """Normalize the Phase 1 spec into the Phase 2 resolved contract."""
    destinations: list[DestinationV2] = []
    if spec.destination.type == "pipedrive":
        destinations.append(
            DestinationV2(
                type="pipedrive",
                enabled=True,
                credential_ref=spec.destination.pipedrive.api_key_ref if spec.destination.pipedrive else None,
                field_mapping=(
                    spec.destination.pipedrive.field_mapping
                    if spec.destination.pipedrive
                    else "standard"
                ),
            )
        )
    else:
        destinations.append(DestinationV2(type="excel", enabled=True))
    if spec.destination.fallback == "excel" and not any(d.type == "excel" for d in destinations):
        destinations.append(DestinationV2(type="excel", enabled=True))

    return CampaignSpecV2(
        identity=SpecIdentity(
            campaign_id=spec.campaign_id,
            name=spec.campaign_id,
            client_name=spec.client.name,
            vertical=spec.targeting.industry,
        ),
        target_profile=TargetProfileV2(
            industry=spec.targeting.industry,
            geography=spec.discovery.geography,
            service_area=spec.client.service_area,
            buyer_personas=spec.targeting.buyer_personas,
            negative_keywords=spec.targeting.negative_keywords,
        ),
        lead_pattern=LeadPattern(
            type="event_signal",
            description="Article/news event signals resolved from the Phase 1 campaign spec.",
        ),
        signals=SignalConfig(
            trigger_signals=spec.targeting.trigger_signals,
            negative_keywords=spec.targeting.negative_keywords,
        ),
        sources=SourceConfig(
            search_queries=spec.discovery.search_queries,
            source_tags=spec.discovery.source_tags,
            geography=spec.discovery.geography,
        ),
        qualification=QualificationV2(
            relevance_rubric=spec.qualification.relevance_rubric,
            min_confidence=spec.qualification.min_confidence,
        ),
        enrichment=EnrichmentV2(
            buyer_persona=spec.enrichment.buyer_persona,
            outreach_angle=spec.enrichment.outreach_angle,
            required_fields=spec.enrichment.required_fields,
        ),
        destinations=destinations,
        run_policy=RunPolicy(
            cadence=spec.schedule.cadence,
            preview_before_push=spec.schedule.preview_before_push,
            max_first_push=spec.schedule.max_first_push,
        ),
        quality_gates=QualityGates(
            min_confidence=spec.qualification.min_confidence,
            preserve_phase1_parity=spec.campaign_id == DEFAULT_CAMPAIGN_ID,
            require_human_preview=spec.schedule.preview_before_push,
        ),
    )


def resolve_spec_path(identifier_or_path: str | Path | None = None) -> Path:
    """Resolve a campaign id, YAML filename, or explicit path to a spec file.

    Examples:
      - None -> campaigns/aether-cleaning-az.yaml
      - "aether-cleaning-az" -> campaigns/aether-cleaning-az.yaml
      - "campaigns/aether-cleaning-az.yaml" -> that path
    """
    raw = identifier_or_path or DEFAULT_CAMPAIGN_ID
    path = Path(raw)
    if path.is_absolute():
        return path
    if path.suffix in {".yaml", ".yml"}:
        if path.parent == Path("."):
            return CAMPAIGNS_DIR / path.name
        return ROOT / path
    if path.parent != Path("."):
        return ROOT / path
    return CAMPAIGNS_DIR / f"{raw}.yaml"


def load_spec(path: str | Path) -> CampaignSpec:
    """Load + validate a campaign spec from a YAML file.

    Raises pydantic.ValidationError on a malformed spec — callers should let it
    surface rather than ship a half-defined campaign.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CampaignSpec.model_validate(data)


def load_campaign_spec(identifier_or_path: str | Path | None = None) -> CampaignSpec:
    """Load a CampaignSpec by id/path, defaulting to the current cleaning spec."""
    return load_spec(resolve_spec_path(identifier_or_path))


def load_campaign_spec_v2(identifier_or_path: str | Path | None = None) -> CampaignSpecV2:
    """Load a v2 spec or normalize an existing Phase 1 spec into v2.

    Existing production code keeps using load_campaign_spec(). Phase 2A tools use
    this resolver so the new engine contracts can evolve without breaking the
    working Aether pipeline.
    """
    path = resolve_spec_path(identifier_or_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("schema_version") == "campaign_spec.v2":
        return CampaignSpecV2.model_validate(data)
    return campaign_spec_v1_to_v2(CampaignSpec.model_validate(data))
