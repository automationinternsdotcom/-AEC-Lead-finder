"""URL → cleaned article text → (Claude-produced) ExtractedArticle, then qualify, then estimate deal size.

The LLM extraction step now happens in-context inside the daily Claude routine —
this module provides only the deterministic pieces:
  - extract_article_text(url, http) — HTTP fetch + trafilatura cleanup
  - is_qualifying(article)          — drop rules on a Claude-produced ExtractedArticle
  - estimate_deal_size(article, rates) — janitorial rate calc

Reuses: httpx, trafilatura, ExtractedArticle (pydantic, still validates Claude's JSON).
Extend: SYSTEM_PROMPT moved to skill/aether_daily_routine.md (the routine's prompt).
"""
from __future__ import annotations

import httpx
import trafilatura

from schema import ExtractedArticle

MIN_CLEAN_CHARS = 200          # paywalled / empty → skip
MAX_CLEAN_CHARS = 8000         # ~2k tokens, plenty for in-context extraction


class ExtractError(RuntimeError):
    """Raised when an article cannot be turned into cleaned text."""


# ── Stage 1: text extraction (no LLM) ─────────────────────────────────────────

def extract_article_text(url: str, http: httpx.Client) -> str:
    """GET article, clean HTML, return text. Caps at MAX_CLEAN_CHARS.

    Raises ExtractError on http >= 400, empty/short content, or paywall.
    """
    resp = http.get(url)
    if resp.status_code >= 400:
        raise ExtractError(f"http {resp.status_code}")
    text = trafilatura.extract(
        resp.text, include_comments=False, include_tables=False, with_metadata=False,
    )
    if not text or len(text) < MIN_CLEAN_CHARS:
        raise ExtractError("empty_or_short")
    return text[:MAX_CLEAN_CHARS]


# ── Stage 2: qualification (drop rules) ───────────────────────────────────────

OTHER_MIN_CONFIDENCE = 0.6      # signal_type='other' is noisier; demand more proof
GENERAL_MIN_CONFIDENCE = 0.5    # baseline LLM confidence floor

DROP_RULES = (
    (lambda a: not a.az_relevant,                                                "not_az"),
    (lambda a: a.signal_type == "other" and a.confidence < OTHER_MIN_CONFIDENCE, "other_low_conf"),
    (lambda a: a.confidence < GENERAL_MIN_CONFIDENCE,                            "low_conf"),
)


def is_qualifying(article: ExtractedArticle) -> tuple[bool, str | None]:
    for predicate, reason in DROP_RULES:
        if predicate(article):
            return False, reason
    return True, None


# ── Stage 3: deal-size estimation (deterministic janitorial rates) ────────────

SQFT_CAP = 5_000_000           # ≈ Sky Harbor terminal; bigger = treat as hallucination
UNIT_MONTHLY_RATE_USD = 120    # multifamily $/door/month
DOLLAR_VALUE_SHARE = 0.002     # janitorial as fraction of construction $


def estimate_deal_size(
    article: ExtractedArticle, rates: dict[str, float],
) -> tuple[int | None, str]:
    """Annualized USD janitorial estimate. Basis populates the Pipedrive Note."""
    sqft = article.square_footage or 0
    if 0 < sqft <= SQFT_CAP and article.property_type in rates:
        return int(sqft * rates[article.property_type] * 12), "sqft"
    if article.unit_count:
        return int(article.unit_count * UNIT_MONTHLY_RATE_USD * 12), "units"
    if article.dollar_value:
        return int(article.dollar_value * DOLLAR_VALUE_SHARE), "dollar"
    return None, "none"
