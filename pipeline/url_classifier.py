"""Deterministic URL type classification for discovered sources."""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

UrlKind = Literal[
    "article",
    "rss_feed",
    "atom_feed",
    "sitemap",
    "permit_listing",
    "market_report",
    "homepage",
    "search_result",
    "directory",
    "public_database",
    "unsupported",
    "other",
]

_KNOWN_TYPES = {
    "article",
    "rss_feed",
    "atom_feed",
    "sitemap",
    "permit_listing",
    "market_report",
    "homepage",
    "search_result",
    "directory",
    "public_database",
    "unsupported",
    "other",
}


def classify_url(url: str, hinted_type: str | None = None) -> UrlKind:
    """Classify a discovered URL without trusting the model blindly."""
    if hinted_type in _KNOWN_TYPES and hinted_type != "other":
        return hinted_type  # type: ignore[return-value]

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return "unsupported"
    path = parsed.path.lower().rstrip("/")
    query = parsed.query.lower()

    if path.endswith((".xml", "/sitemap")) or "sitemap" in path:
        return "sitemap"
    if path.endswith((".rss", ".atom")) or path.endswith("/feed") or "feed" in path:
        return "rss_feed"
    if any(token in path for token in ("permit", "planning", "development-activity")):
        return "permit_listing"
    if any(token in path for token in ("market-report", "research", "insights")):
        return "market_report"
    if parsed.hostname and path in {"", "/"}:
        return "homepage"
    if "search" in path or "q=" in query:
        return "search_result"
    return "article"
