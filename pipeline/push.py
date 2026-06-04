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

from pipeline import automations, config, util
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
        self._settings = settings
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

    def post_v2(self, resource: str, payload: dict) -> None:
        """POST to Pipedrive v2 endpoints while preserving query-token auth."""
        self._req(
            "POST",
            f"https://{self._settings.pipedrive_domain}.pipedrive.com/api/v2/{resource}",
            json=payload,
        )

    def get(self, resource: str, id) -> dict:
        """GET {resource}/{id} → the entity's data dict."""
        return self._req("GET", f"{resource}/{id}")

    def put(self, resource: str, id, payload: dict) -> dict:
        """PUT {resource}/{id} (full update; Persons use PUT)."""
        return self._req("PUT", f"{resource}/{id}", json=payload)

    def patch(self, resource: str, id, payload: dict) -> dict:
        """PATCH {resource}/{id} (partial update; Leads use PATCH)."""
        return self._req("PATCH", f"{resource}/{id}", json=payload)

    def find_lead_by_url(self, article_url: str) -> str | None:
        """Search Leads by Article URL value across all custom fields. Returns UUID or None.

        Pipedrive's /leads/search `fields` param only accepts the literal values
        "custom_fields", "notes", or "title" — it does NOT accept individual
        custom-field hashes. So we scope to `custom_fields` (all of them) and
        post-filter the hits.

        Note: Pipedrive's search index has ~seconds of lag, so a Lead created
        moments earlier may not yet be findable. The orchestrator's SQLite
        seen_urls table is the primary dedup gate; this is the cross-run
        safety net.

        Pipedrive rejects long URL-encoded `term` values with HTTP 400. The
        raw-string truncation below is aggressive (100 chars) but doesn't
        precisely target the encoded limit — heavy-escaping URLs may still
        400, which is why we tolerate 400 as "not found" below. SQLite
        seen_urls is the primary intra-run dedup gate, so the worst case is
        a rare duplicate Lead.

        Hit verification: when the configured Article URL custom field is
        present in the lead's custom_fields, prefer matching against THAT key
        — guards against a future custom field containing similar URLs being
        mistaken for a dedup match. Falls back to scanning all custom_field
        values when the field-keyed lookup misses (covers Pipedrive responses
        that return custom_fields as a flat list rather than a dict).
        """
        term = article_url[:100]
        try:
            items = self._req(
                "GET", "leads/search",
                params={
                    "term": term,
                    "fields": "custom_fields",
                },
            ).get("items", [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                return None
            raise
        field_key = self._settings.pipedrive_field_article_url
        # _lead_payload stores the Article URL capped at 255 chars (Pipedrive's
        # varchar limit). Match against the same cap, or a lead created from an
        # unresolvable >255-char URL would never be found and gets re-created.
        needle = article_url[:255]
        for hit in items:
            item = hit.get("item") or {}
            cf = item.get("custom_fields")
            if cf is None:
                continue
            # Preferred path: custom_fields is a dict keyed by field hash.
            if isinstance(cf, dict):
                v = cf.get(field_key)
                if v == needle or (isinstance(v, dict) and v.get("value") == needle):
                    return item["id"]
                continue
            # Fallback: list shape — scan for the URL value, accept either string
            # or {value: ...} entry. Doesn't distinguish field origin, but the
            # list shape doesn't expose field keys to us.
            for v in cf:
                if isinstance(v, str) and v == needle:
                    return item["id"]
                if isinstance(v, dict) and v.get("value") == needle:
                    return item["id"]
        return None


def sync_to_pipedrive(
    article: ExtractedArticle, lead: Lead | None,
    est_value: int | None, basis: str, url: str, settings: Settings,
    extra_contacts: list[Lead] | None = None,
) -> tuple[int | None, int | None, str]:
    """Upsert org → person → Lead (+ note). Returns (org_id, person_id, lead_id).

    Returns (None, None, existing_id) if a Lead with this article_url already
    exists — caller treats that as 'skipped' rather than 'created'.

    Pipedrive Leads REQUIRE either organization_id or person_id. The pipeline
    relies on extract.py producing a non-empty company_name (the
    ExtractedArticle pydantic schema enforces this), so the org upsert always
    succeeds.

    `extra_contacts` (optional) — up to 3 Lead objects to populate the
    "Lead 1 / Lead 2 / Lead 3" custom fields with `Name | Title | Email | Phone`
    strings. The first contact in [lead] + extra_contacts becomes Lead 1, etc.
    Falls back silently when the Lead-1/2/3 field hashes aren't configured.
    """
    # After filtering, contacts[0] (if any) is the primary used BOTH for the
    # Person record and the Lead 1 field — keep them consistent so a stray
    # generic doesn't end up linked as a Person while Lead 1 shows someone else.
    contacts = _all_contacts(lead, extra_contacts)
    primary = contacts[0] if contacts else None
    if settings.dry_run:
        util.log_event(
            "dry_run_write", url=url, company=article.company_name,
            lead_title=_lead_title(article), value=est_value or 0,
            basis=basis, lead=(primary.name if primary else None),
            extra_contact_count=max(0, len(contacts) - 1),
        )
        return DRY_ORG_ID, (DRY_PERSON_ID if primary else None), DRY_LEAD_ID

    with PipedriveClient(settings) as pd:
        existing = pd.find_lead_by_url(url)
        if existing is not None:
            return None, None, existing

        org_id = _upsert_org(pd, article)
        person_id = _upsert_person(pd, primary, org_id) if primary else None
        lead_id = pd.post_id("leads", _lead_payload(
            article, est_value, org_id, person_id, settings, url, contacts,
        ))
        pd.post("notes", {"lead_id": lead_id, "content": _note_body(article, primary, basis, url)})
        if settings.pipedrive_enable_automations:
            for activity in automations.build_followup_activities(
                lead_id=lead_id,
                org_id=org_id,
                person_id=person_id,
                article=article,
                primary=primary,
                url=url,
                owner_id=settings.pipedrive_automation_owner_id,
            ):
                pd.post_v2("activities", activity)
    return org_id, person_id, lead_id


def update_lead_contacts(
    article_url: str, lead: Lead | None, extra_contacts: list[Lead] | None,
    settings: Settings,
) -> dict:
    """Sync improved contacts into an ALREADY-CREATED Lead (idempotent re-run /
    Heavy-pass upgrade). Finds the Lead by Article URL, then:
      - PUTs the linked Person with the primary's VERIFIED email/phone only
        (same verified-only policy as creation), and
      - PATCHes the Lead 1/2/3 custom fields with the formatted contacts.

    Returns {lead_id, person_id, updated: bool, reason?}. No-op (updated=False)
    when the Lead can't be found by URL.
    """
    contacts = _all_contacts(lead, extra_contacts)
    primary = contacts[0] if contacts else None

    if settings.dry_run:
        util.log_event(
            "dry_run_update", url=article_url,
            primary=(primary.name if primary else None),
            contact_count=len(contacts),
        )
        return {"lead_id": DRY_LEAD_ID, "person_id": DRY_PERSON_ID,
                "updated": True, "dry_run": True}

    with PipedriveClient(settings) as pd:
        lead_id = pd.find_lead_by_url(article_url)
        if lead_id is None:
            return {"lead_id": None, "person_id": None, "updated": False,
                    "reason": "lead_not_found"}

        lead_obj = pd.get("leads", lead_id)
        person_id = lead_obj.get("person_id")
        if isinstance(person_id, dict):  # Pipedrive may nest it as {"value": id}
            person_id = person_id.get("value")

        # Update the Person with verified-only email/phone.
        if person_id and primary:
            email = primary.email if (primary.email and primary.email_verified) else None
            phone = primary.phone if (primary.phone and primary.phone_verified) else None
            body = {}
            if email:
                body["email"] = [{"value": email}]
            if phone:
                body["phone"] = [{"value": phone}]
            if body:
                pd.put("persons", person_id, body)

        # Update Lead 1/2/3 reference text (shows hedged guesses, marked "Likely").
        lead_field_hashes = (
            settings.pipedrive_field_lead_1,
            settings.pipedrive_field_lead_2,
            settings.pipedrive_field_lead_3,
        )
        patch = {field: _fmt_contact(c)
                 for c, field in zip(contacts, lead_field_hashes) if field}
        linkedin_field_hashes = (
            settings.pipedrive_field_lead_1_linkedin,
            settings.pipedrive_field_lead_2_linkedin,
            settings.pipedrive_field_lead_3_linkedin,
        )
        patch.update(
            {field: c.linkedin_url for c, field in zip(contacts, linkedin_field_hashes)
             if field and c.linkedin_url}
        )
        if patch:
            pd.patch("leads", lead_id, patch)

    return {"lead_id": lead_id, "person_id": person_id, "updated": True}


def _all_contacts(primary: Lead | None, extras: list[Lead] | None) -> list[Lead]:
    """Merge primary + extras, drop None entries. Caps at 3 (Pipedrive has
    Lead 1/2/3 custom fields).

    Defensively filters out leads whose names look like job-title placeholders
    rather than specific people — the Grok enricher's skill is supposed to
    strip these but this is a belt-and-suspenders guard against polluted
    contact fields ("Property Manager | Property Manager | ...").
    """
    from pipeline.grok_parse import is_lead_generic
    out: list[Lead] = []
    if primary is not None and not is_lead_generic(primary):
        out.append(primary)
    if extras:
        out.extend(c for c in extras if not is_lead_generic(c))
    return out[:3]


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
    # Verified-only policy: only attach an email/phone to the structured Person
    # record when it's verified. Hedged ("Likely") guesses stay out of the CRM's
    # contact fields — they remain in the Lead 1/2/3 reference text via
    # _fmt_contact — so a human verifies before any outreach.
    email = lead.email if (lead.email and lead.email_verified) else None
    phone = lead.phone if (lead.phone and lead.phone_verified) else None
    # Dedup only on a verified email — searching by a guessed address risks
    # merging into the wrong existing Person.
    if email:
        existing = pd.search_id("persons", term=email, fields="email")
        if existing is not None:
            return existing
    return pd.post_id("persons", {
        "name": lead.name,
        "org_id": org_id,
        "email": [{"value": email}] if email else [],
        "phone": [{"value": phone}] if phone else [],
    })


def _lead_title(a: ExtractedArticle) -> str:
    """Lead title is the news article headline. Trimmed to Pipedrive's
    255-char varchar limit. Older runs used 'Org — signal — city' but Jordan's
    daily review surface is the article headline, so we surface that directly."""
    return a.title[:255]


def _fmt_contact(c: Lead) -> str:
    """`Name | Title | LinkedIn | Email | Phone` — joined with ` | `, null
    parts omitted. Capped at 255 chars (Pipedrive varchar limit)."""
    parts = [c.name]
    if c.title:
        parts.append(c.title)
    if c.linkedin_url:
        parts.append(f"LinkedIn: {c.linkedin_url}")
    if c.email:
        # Mark hedged guesses so a human knows to verify before using them.
        parts.append(c.email if c.email_verified else f"Likely {c.email}")
    if c.phone:
        parts.append(c.phone)
    return " | ".join(parts)[:255]


def _lead_payload(
    a: ExtractedArticle, est_value: int | None,
    org_id: int, person_id: int | None, settings: Settings, url: str,
    contacts: list[Lead] | None = None,
) -> dict:
    # Lead `value` shape is {amount, currency} dict (Deals use flat
    # value+currency at top level — easy footgun if you copy from Deal code).
    payload = {
        "title": _lead_title(a),
        "organization_id": org_id,
        # Pipedrive Text custom fields hard-cap at 255 chars; unresolved Google
        # News RSS URLs blow past that and the API 400s the whole Lead. Cap
        # defensively — callers should pass the resolved publisher URL.
        settings.pipedrive_field_article_url: url[:255],
    }
    if est_value:
        payload["value"] = {"amount": est_value, "currency": "USD"}
    if person_id is not None:
        payload["person_id"] = person_id
    if a.published_date and settings.pipedrive_field_date_posted:
        # Pipedrive date fields want ISO "YYYY-MM-DD" strings; schema stores
        # this as a datetime.date — convert here.
        payload[settings.pipedrive_field_date_posted] = a.published_date.isoformat()

    # Lead 1 / 2 / 3 custom fields — only populated if both the contact
    # exists AND the field hash is configured.
    lead_field_hashes = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )
    for c, field in zip(contacts or [], lead_field_hashes):
        if field:
            payload[field] = _fmt_contact(c)
    linkedin_field_hashes = (
        settings.pipedrive_field_lead_1_linkedin,
        settings.pipedrive_field_lead_2_linkedin,
        settings.pipedrive_field_lead_3_linkedin,
    )
    for c, field in zip(contacts or [], linkedin_field_hashes):
        if field and c.linkedin_url:
            payload[field] = c.linkedin_url
    return payload


def _oneline(s: str | None) -> str:
    """Collapse newlines + tabs so they don't break Pipedrive note line structure.

    LLM-produced fields (summary_2sent, filter_reason, service_angle) can contain
    embedded newlines; without this, every \\n becomes a top-level row in the
    rendered Pipedrive note, breaking the field-per-line format.
    """
    if not s:
        return ""
    return s.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()


def _note_body(a: ExtractedArticle, lead: Lead | None, basis: str, url: str) -> str:
    """Pipedrive Note body. Uses Aether's brand voice (asset preservation,
    strategic partner) via the service_angle field — NOT 'cleaning' / 'janitor'
    language. service_angle is required for high/medium priority articles
    (schema enforces this); only low-priority articles can have it null, and
    those don't reach push.
    """
    lines = [
        _oneline(a.summary_2sent),
        f"Source: {url}",
        f"Signal: {a.signal_type} | Property: {a.property_type} | "
        f"City: {a.city or 'AZ'} | Priority: {a.priority}",
        f"Filter reason: {_oneline(a.filter_reason)}",
    ]
    if a.service_angle:
        lines.append(f"Aether angle: {_oneline(a.service_angle)}")
    lines.append(f"Est-value basis: {basis}")
    if lead:
        lines.append(f"Contact: {lead.name} / {lead.title}")
    else:
        lines.append("Contact: (no decision-maker found)")
    return "\n".join(lines)
