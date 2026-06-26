"""Source discovery contracts and Gemini transcript normalization.

Gemini is allowed to discover candidate sources, not to bypass the engine. This
module converts model output into validated, deduplicated source records that
deterministic fetch/extract stages can consume.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from pipeline import util
from pipeline.contracts import ArtifactEnvelope
from pipeline.spec import CampaignSpecV2, LeadPatternType
from pipeline.transcript_parser import extract_first_json_value
from pipeline.url_normalizer import normalize_provider_url


SourceType = Literal[
    "article",
    "company_page",
    "directory",
    "search_result",
    "public_database",
    "rss_feed",
    "atom_feed",
    "sitemap",
    "homepage",
    "permit_listing",
    "market_report",
    "unsupported",
    "other",
]


class DiscoveryProvider(Protocol):
    provider_name: str

    def parse_transcript(self, text: str, spec: CampaignSpecV2) -> "DiscoveryParseResult":
        """Parse a provider transcript into deterministic source candidates."""


class SourceCandidate(BaseModel):
    url: str
    source_name: str | None = None
    source_type: SourceType = "other"
    title: str | None = None
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_pattern_type: LeadPatternType | None = None
    discovered_via: str = "gemini"
    canonical_url: str | None = None
    url_hash: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_common_provider_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if "source_type" not in out and "type" in out:
            out["source_type"] = out["type"]
        if "suggested_pattern_type" not in out and "pattern_type" in out:
            out["suggested_pattern_type"] = out["pattern_type"]
        if "reason" not in out and "why" in out:
            out["reason"] = out["why"]
        if isinstance(out.get("url"), str):
            out["url"] = normalize_provider_url(out["url"])
        return out

    @model_validator(mode="after")
    def _canonicalize(self) -> "SourceCandidate":
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("source url must be http(s)")
        canonical = util.canonicalize_url(self.url)
        self.canonical_url = canonical
        self.url_hash = util.sha256_hex(canonical)
        if self.source_name is None or not self.source_name.strip():
            self.source_name = parsed.hostname or "unknown_source"
        return self


class DiscoveryParseResult(BaseModel):
    provider: Literal["gemini"] = "gemini"
    candidates: list[SourceCandidate] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)


def parse_gemini_discovery_transcript(
    text: str,
    spec: CampaignSpecV2,
    *,
    min_confidence: float | None = None,
) -> DiscoveryParseResult:
    """Parse Gemini source discovery output.

    Accepts either:
    - a JSON list of source objects
    - an object with `sources`, `urls`, or `records`
    - fenced JSON inside a transcript
    """
    value = extract_first_json_value(text)
    raw_records = _extract_source_records(value)
    threshold = spec.quality_gates.min_confidence if min_confidence is None else min_confidence

    candidates: list[SourceCandidate] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_records:
        try:
            candidate = SourceCandidate.model_validate(raw)
        except Exception as e:
            rejected.append({"record": raw, "reason": f"invalid_source: {e}"})
            continue
        if candidate.confidence < threshold:
            rejected.append({
                "record": raw,
                "reason": "below_confidence_threshold",
            })
            continue
        assert candidate.canonical_url is not None
        if candidate.canonical_url in seen:
            rejected.append({"record": raw, "reason": "duplicate_url"})
            continue
        seen.add(candidate.canonical_url)
        candidates.append(candidate)
    return DiscoveryParseResult(candidates=candidates, rejected=rejected)


def gemini_discovery_to_artifact(
    result: DiscoveryParseResult,
    *,
    campaign_id: str,
    run_id: str,
) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        campaign_id=campaign_id,
        run_id=run_id,
        stage="discover",
        records=[candidate.model_dump(mode="json") for candidate in result.candidates],
        metadata={
            "provider": result.provider,
            "rejected_count": len(result.rejected),
            "rejected": result.rejected,
        },
    )


def records_for_fetch(artifact: ArtifactEnvelope) -> list[dict[str, str]]:
    """Convert discovery artifacts to the fetch CLI URL shape."""
    if artifact.stage != "discover":
        raise ValueError("fetch rows require a discover artifact")
    out: list[dict[str, str]] = []
    for record in artifact.records:
        canonical = record.get("canonical_url") or record["url"]
        url_hash = record.get("url_hash") or util.sha256_hex(util.canonicalize_url(canonical))
        out.append({
            "url_hash": url_hash,
            "url": canonical,
            "source": record.get("source_name") or record.get("discovered_via") or "gemini",
            "title": record.get("title") or "",
        })
    return out


def _extract_source_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("sources", "urls", "records"):
            items = value.get(key)
            if isinstance(items, list):
                return items
    raise ValueError("Gemini discovery output must be a list or object with sources/urls/records")
