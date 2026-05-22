"""Pure / shared helpers: URL canon, hash, UTC time, HTTP client, JSON log.

Reuses: urllib.parse, hashlib, datetime, json (stdlib) + httpx.
Extend here: _TRACK_PREFIXES/_TRACK_EXACT, BROWSER_UA, Google News URL template.
Cross-module consumers: anything that does HTTP fetch, logs an event, or hashes a URL.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import httpx

from pipeline.config import HTTP_TIMEOUT_SEC

BROWSER_UA = "Mozilla/5.0 (compatible; AetherLeadBot/0.1)"

_TRACK_PREFIXES = ("utm_", "mc_")
_TRACK_EXACT = frozenset({"fbclid", "gclid", "yclid", "msclkid", "ref"})


def canonicalize_url(url: str) -> str:
    """Lowercase host, drop fragment + tracking params, normalize trailing slash."""
    if not url or not url.strip():
        raise ValueError("empty url")
    p = urlsplit(url.strip())
    if p.scheme in {"", "data", "javascript"}:
        raise ValueError(f"unsupported scheme: {p.scheme!r}")
    netloc = (p.hostname or "").lower() + (f":{p.port}" if p.port else "")
    path = p.path.rstrip("/") or "/"
    kept = sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not (k.startswith(_TRACK_PREFIXES) or k in _TRACK_EXACT)
    )
    return urlunsplit((p.scheme, netloc, path, urlencode(kept), ""))


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_google_news_url(query: str) -> str:
    """Ported from legacy skill/fetch_feeds.py — do not alter params."""
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )


def make_http_client() -> httpx.Client:
    """Browser-like httpx.Client used for every HTML/RSS fetch in the pipeline."""
    return httpx.Client(
        timeout=HTTP_TIMEOUT_SEC, follow_redirects=True,
        headers={"User-Agent": BROWSER_UA},
    )


def log_event(event: str, **fields) -> None:
    """Single-line JSON log to stdout. The only logger the pipeline uses."""
    print(json.dumps({"event": event, **fields}, default=str))
