"""Apollo people search; returns the top decision-maker for a domain or None.

Reuses: httpx (POST + JSON + timeout), dataclass for Lead, config.Settings.
Extend: APOLLO_URL, DEFAULT_TITLES, DEFAULT_SENIORITIES, SENIORITY_WEIGHT.
Failure: 401/403 raises (config problem); anything else returns None and lets
         the caller stamp lead_gap=True on the deal.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from pipeline import config
from pipeline.config import Settings

APOLLO_URL = "https://api.apollo.io/v1/mixed_people/search"

DEFAULT_TITLES = [
    "facilities", "operations", "property manager",
    "general manager", "asset manager", "owner",
]
DEFAULT_SENIORITIES = ["owner", "founder", "c_suite", "vp", "director", "manager"]
SENIORITY_WEIGHT = {
    "owner": 5, "founder": 4, "c_suite": 4, "vp": 3, "director": 2, "manager": 1,
}


@dataclass(slots=True)
class Lead:
    name: str
    title: str
    email: str | None
    phone: str | None
    linkedin_url: str | None
    seniority: str
    apollo_id: str


class ApolloError(RuntimeError):
    """Hard-fail auth/config errors. Soft failures return None instead."""


def find_lead(domain: str | None, settings: Settings) -> Lead | None:
    """Return the highest-ranked person at `domain`, or None if Apollo can't help.

    Returns None unconditionally if APOLLO_API_KEY is not configured —
    dev/sandbox runs without an Apollo subscription still create deals
    (with lead_gap=True), they just don't get enriched.
    """
    if not domain or not settings.apollo_api_key:
        return None
    body = {
        "q_organization_domains": domain,
        "person_titles": DEFAULT_TITLES,
        "person_seniorities": DEFAULT_SENIORITIES,
        "per_page": 5,
    }
    headers = {"X-Api-Key": settings.apollo_api_key, "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT_SEC) as c:
            resp = c.post(APOLLO_URL, headers=headers, json=body)
    except httpx.HTTPError:
        return None
    if resp.status_code in (401, 403):
        raise ApolloError(f"apollo auth failed: {resp.status_code}")
    if resp.status_code != 200:
        return None
    people = resp.json().get("people") or []
    if not people:
        return None
    top = max(people, key=lambda p: SENIORITY_WEIGHT.get(p.get("seniority") or "", 0))
    phones = top.get("phone_numbers") or []
    return Lead(
        name=top.get("name") or "",
        title=top.get("title") or "",
        email=top.get("email"),
        phone=(phones[0].get("sanitized_number") if phones else None),
        linkedin_url=top.get("linkedin_url"),
        seniority=top.get("seniority") or "",
        apollo_id=str(top.get("id") or ""),
    )
