"""Normalize provider-emitted URLs before deterministic pipeline validation."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit


_MARKDOWN_LINK_RE = re.compile(r"^\s*\[([^\]]+)\]\(([^)]+)\)\s*$")
_RAW_URL_RE = re.compile(r"https?://[^\s<>\])]+", re.IGNORECASE)


def normalize_provider_url(value: str) -> str:
    """Return a raw destination URL from Gemini/browser-style output.

    Gemini sometimes returns a Markdown link inside the JSON `url` field, and
    search-grounded responses may wrap the real URL in a Google search URL. The
    rest of the pipeline expects a plain http(s) URL, so this function repairs
    those common shapes before canonicalization and validation.
    """
    url = value.strip().strip("<>")
    markdown = _MARKDOWN_LINK_RE.match(url)
    if markdown:
        label, href = markdown.groups()
        label = label.strip().strip("<>")
        href = href.strip().strip("<>")
        url = label if _looks_like_http_url(label) else href

    url = _unwrap_google_search_url(url)
    if not _looks_like_http_url(url):
        embedded = _RAW_URL_RE.search(url)
        if embedded:
            url = _unwrap_google_search_url(embedded.group(0))
    return url.strip()


def _looks_like_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _unwrap_google_search_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host not in {"google.com", "www.google.com"} or parsed.path != "/search":
        return url

    query = parse_qs(parsed.query)
    for key in ("q", "url", "u"):
        values = query.get(key)
        if not values:
            continue
        candidate = unquote(values[0]).strip()
        if _looks_like_http_url(candidate):
            return candidate
    return url
