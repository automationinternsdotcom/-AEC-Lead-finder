"""Sync one extracted article → Pipedrive Org + Person + Lead (+ note).

Pipedrive Leads are the right surface for machine-extracted, unvetted CRE
inputs: Jordan triages them and converts promising ones to Deals. Pushing
straight to Deals (earlier design) would have polluted his active pipeline
with ~50/day of noise and lost the conversion-as-qualification signal.

Reuses: httpx (base_url + querystring api_token does the auth wiring once),
        config.Settings, schema.ExtractedArticle, enrich.Lead.
Extend: PipedriveClient gives 3 generic verbs (search_id / post_id / post).
        Add a new endpoint? Just call them with a different resource string.
Dedup: org by exact name; person by email (if present). Lead dedup is the
       orchestrator's job (it skips URLs already marked status='pushed').
       Pipedrive-side dedup via find_lead_by_url is a safety net.
Failure: 401/403 → PipedriveError. Network / 5xx → raise (per-article
         try/except in the orchestrator keeps the rest of the batch alive).
"""
from __future__ import annotations

import httpx

from pipeline import config, util
from pipeline.config import Settings
from pipeline.enrich import Lead
from schema import ExtractedArticle

# Pipedrive Lead IDs are UUID strings (not ints, unlike Deal IDs). Dry-run
# sentinels match the shape so callers don't crash on type assumptions.
DRY_ORG_ID, DRY_PERSON_ID = -1, -2
DRY_LEAD_ID = "dry-run-lead-id"


class PipedriveError(RuntimeError):
    """Hard-fail auth/server errors."""


class PipedriveClient:
    """REST wrapper. Three generic verbs cover every endpoint we touch."""

    def __init__(self, settings: Settings):
        # Trailing slash + relative paths: httpx joins per RFC 3986. Absolute
        # paths (leading "/") would REPLACE base_url's /api/v1 — don't do that.
        self._http = httpx.Client(
            base_url=f"https://{settings.pipedrive_domain}.pipedrive.com/api/v1/",
            timeout=config.HTTP_TIMEOUT_SEC,
            params={"api_token": settings.pipedrive_api_token},
        )

    def __enter__(self) -> "PipedriveClient":
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    def _req(self, method: str, path: str, **kwargs) -> dict:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code in (401, 403):
            raise PipedriveError(f"auth failed: {resp.status_code} on {path}")
        resp.raise_for_status()
        payload = resp.json()
        # Pipedrive returns HTTP 200 with {"success": false, "error": "..."}
        # on validation failures. Without this check, search_id/post silently
        # drop the call and post_id crashes downstream with KeyError('id').
        if payload.get("success") is False:
            raise PipedriveError(
                f"{method} {path} success:false — "
                f"{payload.get('error') or payload.get('error_info') or payload}"
            )
        return payload.get("data") or {}

    def search_id(self, resource: str, **params):
        """GET {resource}/search → first hit's id (int or UUID-str), or None."""
        data = self._req("GET", f"{resource}/search", params=params)
        items = data.get("items") if isinstance(data, dict) else None
        return items[0]["item"]["id"] if items else None

    def post_id(self, resource: str, payload: dict):
        """POST {resource} → created entity's id (int for orgs/persons, str for leads)."""
        return self._req("POST", resource, json=payload)["id"]

    def post(self, resource: str, payload: dict) -> None:
        """POST {resource} with no return value (notes, activities, etc.)."""
        self._req("POST", resource, json=payload)

    def find_lead_by_url(self, article_url: str) -> str | None:
        """Search Leads by Article URL value across all custom fields. Returns UUID or None.

        Pipedrive's /leads/search `fields` param only accepts the literal values
        "custom_fields", "notes", or "title" — it does NOT accept individual
        custom-field hashes. So we scope to `custom_fields` (all of them); URL
        strings are unique enough that a cross-field collision is negligible.

        Note: Pipedrive's search index has ~seconds of lag, so a Lead created
        moments earlier may not yet be findable. The orchestrator's SQLite
        seen_urls table is the primary dedup gate; this is the cross-run
        safety net.
        """
        items = self._req(
            "GET", "leads/search",
            params={
                "term": article_url, "exact_match": "true",
                "fields": "custom_fields",
            },
        ).get("items", [])
        return items[0]["item"]["id"] if items else None


def sync_to_pipedrive(
    article: ExtractedArticle, lead: Lead | None,
    est_value: int | None, basis: str, url: str, settings: Settings,
) -> tuple[int | None, int | None, str]:
    """Upsert org → person → Lead (+ note). Returns (org_id, person_id, lead_id).

    Returns (None, None, existing_id) if a Lead with this article_url already
    exists — caller treats that as 'skipped' rather than 'created'.

    Pipedrive Leads REQUIRE either organization_id or person_id. The pipeline
    relies on extract.py producing a non-empty company_name (the
    ExtractedArticle pydantic schema enforces this), so the org upsert always
    succeeds.
    """
    if settings.dry_run:
        util.log_event(
            "dry_run_write", url=url, company=article.company_name,
            lead_title=_lead_title(article), value=est_value or 0,
            basis=basis, lead=(lead.name if lead else None),
        )
        return DRY_ORG_ID, (DRY_PERSON_ID if lead else None), DRY_LEAD_ID

    with PipedriveClient(settings) as pd:
        existing = pd.find_lead_by_url(url)
        if existing is not None:
            return None, None, existing

        org_id = _upsert_org(pd, article)
        person_id = _upsert_person(pd, lead, org_id) if lead else None
        lead_id = pd.post_id("leads", _lead_payload(
            article, est_value, org_id, person_id, settings, url,
        ))
        pd.post("notes", {"lead_id": lead_id, "content": _note_body(article, lead, basis, url)})
    return org_id, person_id, lead_id


def _upsert_org(pd: PipedriveClient, a: ExtractedArticle) -> int:
    existing = pd.search_id("organizations", term=a.company_name, exact_match="true")
    # Use `is not None` rather than truthy — Pipedrive IDs start at 1 in practice
    # but `0 or create()` would create on the impossible-but-defensive path.
    if existing is not None:
        return existing
    return pd.post_id(
        "organizations", {"name": a.company_name, "address": a.address or ""},
    )


def _upsert_person(pd: PipedriveClient, lead: Lead, org_id: int) -> int:
    if lead.email:
        existing = pd.search_id("persons", term=lead.email, fields="email")
        if existing is not None:
            return existing
    return pd.post_id("persons", {
        "name": lead.name,
        "org_id": org_id,
        "email": [{"value": lead.email}] if lead.email else [],
        "phone": [{"value": lead.phone}] if lead.phone else [],
    })


def _lead_title(a: ExtractedArticle) -> str:
    return f"{a.company_name} — {a.signal_type} — {a.city or 'AZ'}"


def _lead_payload(
    a: ExtractedArticle, est_value: int | None,
    org_id: int, person_id: int | None, settings: Settings, url: str,
) -> dict:
    # Lead `value` shape is {amount, currency} dict (Deals use flat
    # value+currency at top level — easy footgun if you copy from Deal code).
    payload = {
        "title": _lead_title(a),
        "organization_id": org_id,
        settings.pipedrive_field_article_url: url,
    }
    if est_value:
        payload["value"] = {"amount": est_value, "currency": "USD"}
    if person_id is not None:
        payload["person_id"] = person_id
    return payload


def _note_body(a: ExtractedArticle, lead: Lead | None, basis: str, url: str) -> str:
    return "\n".join([
        a.summary_2sent,
        f"Source: {url}",
        f"Signal: {a.signal_type} | Property: {a.property_type} | City: {a.city or 'AZ'}",
        f"Est-value basis: {basis}",
        f"Lead: {lead.name + ' / ' + lead.title if lead else 'lead_gap'}",
    ])
