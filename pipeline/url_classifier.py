"""Deterministic URL type classification for discovered sources."""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

UrlKind = Literal[
    "article",
    "rss_feed",
    "atom_feed",
    "sitemap",
    "source_listing",
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
    "source_listing",
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
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return "unsupported"
    path = parsed.path.lower().rstrip("/")
    query = parsed.query.lower()

    if _looks_like_source_listing(path, query):
        return "source_listing"

    if hinted_type in _KNOWN_TYPES and hinted_type != "other":
        return hinted_type  # type: ignore[return-value]

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


def _looks_like_source_listing(path: str, query: str) -> bool:
    """Reusable listing/category pages should be expanded, not fetched once."""
    segments = [segment for segment in path.split("/") if segment]
    if any(segment in {"category", "categories", "tag", "tags"} for segment in segments):
        return True
    if segments and segments[-1] in {
        "news",
        "newsroom",
        "projects",
        "project",
        "press-releases",
        "media",
        "articles",
        "insights",
        "research",
    }:
        return True
    listing_tokens = {
        "commercial-real-estate",
        "commercial-construction",
        "construction",
        "economic-development",
        "development-projects",
        "development-services",
    }
    if segments and segments[-1] in listing_tokens and len(segments) <= 4:
        return True
    if segments and segments[-1] == "projects" and query:
        return True
    return False
