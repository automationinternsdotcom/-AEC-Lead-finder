"""Event-level deduplication core — pure logic, no I/O.

Same-event detection across different-URL articles: a cheap title-token
similarity narrows candidates; Claude (in the daily routine) makes the final
call. Also powers the one-time backfill clustering. See
docs/superpowers/specs/2026-06-17-lead-event-dedup-design.md.
"""
from __future__ import annotations

import re

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
