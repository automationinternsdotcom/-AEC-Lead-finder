"""Sync one extracted article → Pipedrive Org + Person + Deal (+ note).

Reuses: httpx (base_url + querystring api_token does the auth wiring once),
        config.Settings, schema.ExtractedArticle, enrich.Lead.
Extend: PipedriveClient gives 3 generic verbs (search_id / post_id / post).
        Add a new endpoint? Just call them with a different resource string —
        no per-endpoint wrapper method needed.
Dedup: org by exact name; person by email (if present). Deal dedup is the
       orchestrator's job (it skips URLs already marked status='pushed').
Failure: 401/403 → PipedriveError. Network / 5xx → raise (per-article try/except
         in main keeps the rest of the batch alive).
"""
from __future__ import annotations

import httpx

from pipeline import config, util
from pipeline.config import Settings
from pipeline.enrich import Lead
from schema import ExtractedArticle

DRY_ORG_ID, DRY_PERSON_ID, DRY_DEAL_ID = -1, -2, -3

# Future seed_pipedrive.py fills this with {logical_name: pipedrive_field_hash_id}.
# Until populated, descriptive context lives in the deal note (see _note_body).
CUSTOM_FIELDS: dict[str, str] = {}


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

    def search_id(self, resource: str, **params) -> int | None:
        """GET {resource}/search → first hit's id, or None."""
        data = self._req("GET", f"{resource}/search", params=params)
        items = data.get("items") if isinstance(data, dict) else None
        return items[0]["item"]["id"] if items else None

    def post_id(self, resource: str, payload: dict) -> int:
        """POST {resource} → created entity's id."""
        return self._req("POST", resource, json=payload)["id"]

    def post(self, resource: str, payload: dict) -> None:
        """POST {resource} with no return value (notes, activities, etc.)."""
        self._req("POST", resource, json=payload)


def sync_to_pipedrive(
    article: ExtractedArticle, lead: Lead | None,
    est_value: int | None, basis: str, url: str, settings: Settings,
) -> tuple[int, int | None, int]:
    """Upsert org → person → deal (+ note). Returns the three IDs."""
    if settings.dry_run:
        util.log_event(
            "dry_run_write", url=url, company=article.company_name,
            deal_title=_deal_title(article), value=est_value or 0,
            basis=basis, lead=(lead.name if lead else None),
        )
        return DRY_ORG_ID, (DRY_PERSON_ID if lead else None), DRY_DEAL_ID

    with PipedriveClient(settings) as pd:
        org_id = _upsert_org(pd, article)
        person_id = _upsert_person(pd, lead, org_id) if lead else None
        deal_id = pd.post_id("deals", _deal_payload(article, est_value, org_id, person_id, settings))
        pd.post("notes", {"deal_id": deal_id, "content": _note_body(article, lead, basis, url)})
    return org_id, person_id, deal_id


def _upsert_org(pd: PipedriveClient, a: ExtractedArticle) -> int:
    existing = pd.search_id("organizations", term=a.company_name, exact_match="true")
    return existing or pd.post_id(
        "organizations", {"name": a.company_name, "address": a.address or ""},
    )


def _upsert_person(pd: PipedriveClient, lead: Lead, org_id: int) -> int:
    if lead.email:
        existing = pd.search_id("persons", term=lead.email, fields="email")
        if existing:
            return existing
    return pd.post_id("persons", {
        "name": lead.name,
        "org_id": org_id,
        "email": [{"value": lead.email}] if lead.email else [],
        "phone": [{"value": lead.phone}] if lead.phone else [],
    })


def _deal_title(a: ExtractedArticle) -> str:
    return f"{a.company_name} — {a.signal_type} — {a.city or 'AZ'}"


def _deal_payload(
    a: ExtractedArticle, est_value: int | None,
    org_id: int, person_id: int | None, settings: Settings,
) -> dict:
    return {
        "title": _deal_title(a),
        "value": est_value or 0,
        "currency": "USD",
        "org_id": org_id,
        "person_id": person_id,
        "pipeline_id": settings.pipedrive_pipeline_id,
        "stage_id": settings.pipedrive_stage_id,
    }


def _note_body(a: ExtractedArticle, lead: Lead | None, basis: str, url: str) -> str:
    return "\n".join([
        a.summary_2sent,
        f"Source: {url}",
        f"Signal: {a.signal_type} | Property: {a.property_type} | City: {a.city or 'AZ'}",
        f"Est-value basis: {basis}",
        f"Lead: {lead.name + ' / ' + lead.title if lead else 'lead_gap'}",
    ])
