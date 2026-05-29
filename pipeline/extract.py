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

import concurrent.futures

import httpx
import trafilatura
from googlenewsdecoder import gnewsdecoder

from schema import ExtractedArticle

MIN_CLEAN_CHARS = 200          # paywalled / empty → skip
MAX_CLEAN_CHARS = 8000         # ~2k tokens, plenty for in-context extraction

GOOGLE_NEWS_HOSTS = ("news.google.com",)
# Wall-clock timeout for the gnewsdecoder library, which uses its own bare
# `requests` import (no timeout) for two sequential GETs to news.google.com.
# Without a timeout an unresponsive resolution hangs the per-article subprocess.
GNEWS_RESOLVE_TIMEOUT_SEC = 12


class ExtractError(RuntimeError):
    """Raised when an article cannot be turned into cleaned text."""


def _resolve_google_news(url: str) -> str:
    """Google News RSS feed entries are JS-redirect URLs. Decode to the
    publisher's real URL before fetching — httpx's follow_redirects can't
    handle JavaScript-based redirects.

    Wraps the call in a ThreadPoolExecutor because gnewsdecoder uses its own
    `requests` import without timeout configuration — a slow news.google.com
    response would otherwise hang the caller indefinitely.

    `interval=0` skips gnewsdecoder's per-call sleep (the default is 1s, which
    was a 1-second-per-article tax with no rate-limiting benefit — Google
    throttles per-IP, not per-call).
    """
    if not any(h in url for h in GOOGLE_NEWS_HOSTS):
        return url
    # Submit then return without using `with` — the executor's context-manager
    # exit blocks on shutdown(wait=True), which would wait for the hung worker
    # to actually finish. We don't care about it once the timeout fires; it'll
    # die when the subprocess does (per-article CLIs are short-lived).
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(gnewsdecoder, url, interval=0)
    try:
        result = future.result(timeout=GNEWS_RESOLVE_TIMEOUT_SEC)
    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False, cancel_futures=True)
        raise ExtractError(f"gnews_decode_timeout (>{GNEWS_RESOLVE_TIMEOUT_SEC}s)") from None
    except Exception as e:
        pool.shutdown(wait=False, cancel_futures=True)
        raise ExtractError(f"gnews_decode_failed: {e!r}") from e
    pool.shutdown(wait=False)
    if not result.get("status") or not result.get("decoded_url"):
        raise ExtractError(f"gnews_decode_failed: {result.get('message', 'unknown')}")
    return result["decoded_url"]


# ── Stage 1: text extraction (no LLM) ─────────────────────────────────────────

def extract_article_text(url: str, http: httpx.Client) -> str:
    """GET article, clean HTML, return text. Caps at MAX_CLEAN_CHARS.

    Raises ExtractError on http >= 400, empty/short content, or paywall.
    """
    resolved = _resolve_google_news(url)
    resp = http.get(resolved)
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

# Drop rules apply in order; first match wins. The reported reason matches
# the first rule that fired, which determines what shows up in audit logs.
#
# Order rationale (do not reorder without thinking through audit reporting):
#   not_az          — foundational geographic exclusion; if the property
#                     isn't in AZ, the article is out-of-scope regardless
#                     of how Jordan's protocol scored it
#   low_priority    — Jordan's protocol violations (macro commentary,
#                     mortgage news, rankings, etc.) — more actionable for
#                     prompt-tuning than the generic confidence guards
#   other_low_conf  — `signal_type=other` is the catch-all bucket; demand
#                     higher confidence to push it
#   low_conf        — last-resort floor for anything Claude rated poorly
DROP_RULES = (
    (lambda a: not a.az_relevant,                                                "not_az"),
    (lambda a: a.priority == "low",                                              "low_priority"),
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
