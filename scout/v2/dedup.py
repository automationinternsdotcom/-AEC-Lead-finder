"""Completeness-checked deterministic and fuzzy event deduplication."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .ids import normalize_text, stable_hash


class DedupContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DedupGroup:
    kept_id: str
    member_ids: tuple[str, ...]


def event_fingerprint(organization: str, event: str, location: str, event_date: str = "") -> str:
    return stable_hash(
        normalize_text(organization),
        normalize_text(event),
        normalize_text(location),
        str(event_date or "")[:10],
    )


def validate_fuzzy_groups(input_ids: Iterable[str], groups: Iterable[dict]) -> list[DedupGroup]:
    expected = list(input_ids)
    expected_set = set(expected)
    if len(expected) != len(expected_set):
        raise DedupContractError("input IDs must be unique")
    parsed: list[DedupGroup] = []
    seen: list[str] = []
    for raw in groups:
        kept = str(raw.get("kept_id") or "").strip()
        members = tuple(str(value).strip() for value in raw.get("member_ids") or [])
        if not kept or not members or kept not in members:
            raise DedupContractError("each group requires a kept_id included in nonempty member_ids")
        parsed.append(DedupGroup(kept, members))
        seen.extend(members)
    unknown = sorted(set(seen) - expected_set)
    counts = Counter(seen)
    missing = sorted(expected_set - set(seen))
    duplicates = sorted(key for key, count in counts.items() if count != 1)
    if unknown or missing or duplicates:
        raise DedupContractError(
            f"fuzzy groups must cover every ID exactly once; unknown={unknown}, missing={missing}, duplicates={duplicates}"
        )
    return parsed
