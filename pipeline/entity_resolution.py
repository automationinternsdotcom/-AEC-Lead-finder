"""Deterministic entity grouping for Phase 2 entity-aggregation patterns."""
from __future__ import annotations

import re
from collections import defaultdict
from hashlib import sha256
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field, model_validator


_BUSINESS_SUFFIXES = (
    "llc", "l l c",
    "inc", "incorporated",
    "corp", "corporation",
    "ltd", "limited",
    "lp", "l p",
    "llp", "l l p",
    "company", "co",
)
_GENERIC_NAMES = {"owner", "manager", "property manager", "unknown", "n a", "na"}
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_MULTI_SPACE = re.compile(r"\s+")
_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in _BUSINESS_SUFFIXES) + r")\b",
    re.IGNORECASE,
)


class EntityObservation(BaseModel):
    entity_name: str
    source: str
    signal: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    domain: str | None = None
    address: str | None = None
    city: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _entity_name_must_be_specific(self) -> "EntityObservation":
        if not normalize_entity_name(self.entity_name):
            raise ValueError("entity_name must contain a specific entity")
        return self


class EntityGroup(BaseModel):
    entity_key: str
    canonical_name: str
    observations: list[EntityObservation]
    aliases: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_codex_adjudication: bool = False
    adjudication_reason: str | None = None


def normalize_entity_name(name: str) -> str:
    """Normalize names enough for deterministic MVP grouping."""
    s = name.lower().replace("&", " and ")
    s = _NON_ALNUM.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    s = _SUFFIX_RE.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    return "" if s in _GENERIC_NAMES else s


def group_entity_observations(
    observations: list[EntityObservation | dict[str, Any]],
) -> list[EntityGroup]:
    """Group observations by normalized entity name and flag ambiguous groups."""
    parsed = [
        obs if isinstance(obs, EntityObservation) else EntityObservation.model_validate(obs)
        for obs in observations
    ]
    buckets: dict[str, list[EntityObservation]] = defaultdict(list)
    for obs in parsed:
        buckets[normalize_entity_name(obs.entity_name)].append(obs)

    groups = [_build_group(key, bucket) for key, bucket in buckets.items()]
    return sorted(groups, key=lambda g: (-len(g.observations), g.canonical_name.lower()))


def _build_group(key: str, observations: list[EntityObservation]) -> EntityGroup:
    aliases = sorted({obs.entity_name for obs in observations})
    domains = sorted({obs.domain.strip().lower() for obs in observations if obs.domain and obs.domain.strip()})
    cities = sorted({obs.city.strip() for obs in observations if obs.city and obs.city.strip()})
    signals = sorted({obs.signal.strip() for obs in observations if obs.signal and obs.signal.strip()})
    confidence = mean(obs.confidence for obs in observations)
    ambiguous_reasons: list[str] = []
    if len(domains) > 1:
        ambiguous_reasons.append("conflicting domains")
    if len(aliases) > 1 and not domains:
        ambiguous_reasons.append("multiple aliases with no shared domain")
    if len(observations) == 1 and confidence < 0.7:
        ambiguous_reasons.append("single low-confidence observation")

    return EntityGroup(
        entity_key=_entity_key(key),
        canonical_name=_choose_canonical_name(observations),
        observations=observations,
        aliases=aliases,
        domains=domains,
        cities=cities,
        signals=signals,
        confidence=round(confidence, 4),
        needs_codex_adjudication=bool(ambiguous_reasons),
        adjudication_reason="; ".join(ambiguous_reasons) if ambiguous_reasons else None,
    )


def _choose_canonical_name(observations: list[EntityObservation]) -> str:
    return sorted(
        {obs.entity_name for obs in observations},
        key=lambda name: (-len(name), name.lower()),
    )[0]


def _entity_key(normalized: str) -> str:
    digest = sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"entity:{digest}"
