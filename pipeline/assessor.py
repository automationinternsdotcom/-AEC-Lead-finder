"""Maricopa County Assessor parcel lookup by address.

The Maricopa Assessor search page (mcassessor.maricopa.gov/mcs/?q=...) is a
jQuery shell that fetches results via an XHR to /search/rp/?q=... with a
per-page-load token in the Authorization header. We replicate that two-step
flow with httpx — no browser, no captcha (verified live 2026-05-22).

Returns the top result's {owner, mailing_address, apn, property_type} or None.

For non-Maricopa cities (Tucson, Flagstaff, etc.) the function short-circuits
without making any HTTP request — Maricopa Assessor only covers Maricopa County.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

SEARCH_PAGE_URL = "https://mcassessor.maricopa.gov/mcs/"
API_URL = "https://mcassessor.maricopa.gov/search/rp/"

# Cities at least partially inside Maricopa County. Conservative — better to
# query and find nothing than to skip a valid address.
# Cleaning-specific coverage list; do not generalize this from
# CampaignSpec.discovery.geography. A source-plugin coverage model belongs in a
# later productization phase.
_MARICOPA_CITIES = frozenset({
    "phoenix", "tempe", "scottsdale", "mesa", "chandler", "glendale",
    "gilbert", "peoria", "surprise", "avondale", "goodyear", "buckeye",
    "queen creek", "fountain hills", "cave creek", "el mirage", "carefree",
    "litchfield park", "tolleson", "wickenburg", "youngtown", "guadalupe",
    "paradise valley", "apache junction",
})

# One regex per city with word-boundary anchors — avoids false-positive
# substring matches like "phoenix" inside "Phoenixville, PA".
_CITY_PATTERNS = tuple(
    re.compile(rf"\b{re.escape(c)}\b") for c in _MARICOPA_CITIES
)

# Token line from the search page: var g_token = '...';
_TOKEN_RE = re.compile(r"g_token\s*=\s*'([^']+)'")


def _in_maricopa(address: str) -> bool:
    """True iff `address` mentions an AZ city in Maricopa County.

    Uses word-boundary matching so 'Phoenix' doesn't match inside
    'Phoenixville, PA'. Also requires 'az' in the string to filter out
    Maricopa-named cities in other states (e.g., Mesa, CO).
    """
    a = address.lower()
    if " az" not in a and ",az" not in a and a[-2:] != "az":
        return False
    return any(p.search(a) for p in _CITY_PATTERNS)


def lookup_by_address(address: str, http: httpx.Client) -> dict[str, Any] | None:
    """Lookup the top property record matching this address.

    Returns dict with keys 'owner', 'mailing_address', 'apn', 'property_type',
    or None on any failure (non-Maricopa, no token, HTTP error, empty results).
    """
    if not _in_maricopa(address):
        return None

    # Step 1: hit the HTML search page to get a fresh per-session token.
    try:
        page = http.get(SEARCH_PAGE_URL, params={"q": address})
    except httpx.HTTPError:
        return None
    if page.status_code != 200:
        return None
    m = _TOKEN_RE.search(page.text)
    if not m:
        return None
    token = m.group(1)

    # Step 2: call the JSON API with the token in Authorization header.
    try:
        api = http.get(
            API_URL,
            params={"q": address},
            headers={"Authorization": token},
        )
    except httpx.HTTPError:
        return None
    if api.status_code != 200:
        return None
    payload = api.json()
    results = payload.get("Results") or []
    if not results:
        return None

    top = results[0]
    return {
        "apn": top.get("APN"),
        "owner": top.get("Ownership"),
        "mailing_address": _format_situs(top),
        "property_type": top.get("PropertyType"),
    }


def _format_situs(record: dict) -> str | None:
    """Build a 'street, city, zip' string from the situs fields."""
    parts = [
        (record.get("SitusAddress") or "").strip(),
        (record.get("SitusCity") or "").strip(),
        (record.get("SitusZip") or "").strip(),
    ]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None
