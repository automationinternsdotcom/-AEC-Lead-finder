"""Event-level deduplication core — pure logic, no I/O.

Same-event detection across different-URL articles: a cheap title-token
similarity narrows candidates; Claude (in the daily routine) makes the final
call. Also powers the one-time backfill clustering. See
docs/superpowers/specs/2026-06-17-lead-event-dedup-design.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# Unit/measure words and generic filler that carry no event identity.
_NOISE_WORDS = frozenset({
    "square", "feet", "foot", "sqft", "million", "billion", "units", "unit",
    "acre", "acres", "story", "stories", "from", "with", "into", "near",
    "that", "this", "they", "their", "will", "have", "more", "than", "tops",
    "for", "the", "and", "new",
})
# Company suffixes stripped by normalize_company (one trailing word at a time).
_COMPANY_SUFFIXES = frozenset({
    "companies", "company", "construction", "development", "developments",
    "partners", "group", "ventures", "capital", "holdings", "properties",
    "residential", "investments", "associates", "llc", "inc", "lp", "co",
})


def title_tokens(title: str) -> frozenset[str]:
    """Lowercase significant tokens of a headline: drop digits, units, stopwords,
    and tokens shorter than 3 chars."""
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]+", (title or "").lower())
    return frozenset(
        w for w in words if len(w) >= 3 and w not in _NOISE_WORDS
    )


def normalize_company(name: str) -> str:
    """Lowercase a company name, drop parenthetical aliases and legal/suffix
    noise. Conservative: only strips trailing suffix words, never interior ones."""
    n = re.sub(r"\(.*?\)", " ", (name or "").lower())   # drop "(SkySong)"
    n = re.sub(r"[.,]", " ", n)
    parts = [p for p in n.split() if p]
    while len(parts) > 1 and parts[-1] in _COMPANY_SUFFIXES:
        parts.pop()
    return " ".join(parts).strip()


@dataclass(slots=True)
class LeadRecord:
    """Minimal projection of a Pipedrive Lead for dedup. `num_filled` counts
    non-empty meaningful fields for completeness ranking; `add_dt` is the Lead's
    add_time as an aware datetime (or None when unknown)."""
    lead_id: str
    title: str
    url: str | None
    contacts: list[str]
    add_dt: datetime | None
    num_filled: int


def same_event_score(title_a: str, title_b: str) -> float:
    """Jaccard overlap of significant title tokens (0..1)."""
    a, b = title_tokens(title_a), title_tokens(title_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_leads(leads: list[LeadRecord], threshold: float) -> list[list[LeadRecord]]:
    """Connected-components clustering: leads are in the same cluster if a chain
    of pairwise same_event_score >= threshold links them."""
    n = len(leads)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if same_event_score(leads[i].title, leads[j].title) >= threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[LeadRecord]] = {}
    for i, lead in enumerate(leads):
        groups.setdefault(find(i), []).append(lead)
    return list(groups.values())


_MAX_CONTACTS = 3  # Pipedrive Lead 1/2/3 fields


@dataclass(slots=True)
class MergeResult:
    kept: list[str]       # final contact strings to write to Lead 1/2/3
    overflow: list[str]   # unique contacts that didn't fit (logged, not written)


def completeness_key(lead: LeadRecord):
    """Sort key for keeper selection. max() picks: most contacts, then most
    filled fields, then EARLIEST add_time. A missing add_dt loses the date
    tie-break (treated as infinitely late)."""
    epoch = lead.add_dt.timestamp() if lead.add_dt else float("inf")
    return (len(lead.contacts), lead.num_filled, -epoch)


def _contact_name_key(contact: str) -> str:
    """Identity for contact dedup: normalized name segment (before first ' | ')."""
    name = contact.split(" | ", 1)[0]
    return re.sub(r"\s+", " ", name).strip().lower()


def merge_contact_strings(existing: list[str], incoming: list[str]) -> MergeResult:
    """Union existing + incoming contact strings, dedup by name, keeper's first,
    cap at 3. Anything beyond the cap is returned as overflow (never silently
    dropped — callers log it)."""
    kept: list[str] = []
    overflow: list[str] = []
    seen: set[str] = set()
    for contact in [*existing, *incoming]:
        if not contact or not contact.strip():
            continue
        key = _contact_name_key(contact)
        if key in seen:
            continue
        seen.add(key)
        (kept if len(kept) < _MAX_CONTACTS else overflow).append(contact)
    return MergeResult(kept=kept, overflow=overflow)
