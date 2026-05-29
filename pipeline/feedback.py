"""Pipedrive feedback loop — poll for Leads Jordan has flagged as not relevant.

Jordan applies a `NOT RELEVANT` label in Pipedrive when an article-sourced
Lead shouldn't have been pushed. This module gives the daily routine a way
to surface those flags in its run report so the operator can manually tune
the routine's protocol (skill/aether_daily_routine.md Step 2b) over time.

Per the design decision (see commit message): we do NOT auto-blocklist
companies or auto-suppress URLs. The signal is *informational only* —
the operator reads the report and decides what to adjust.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import httpx

# The label name Jordan must create in Pipedrive (Settings -> Lead labels).
# Case-insensitive match.
NOT_RELEVANT_LABEL_NAME = "NOT RELEVANT"


@dataclasses.dataclass(slots=True)
class FlaggedLead:
    """A Lead Jordan tagged with the NOT RELEVANT label."""
    lead_id: str            # Pipedrive Lead UUID
    title: str
    article_url: str | None  # from the Article URL custom field (None if Lead
                             # was created outside the pipeline)
    flagged_at: str          # Pipedrive update_time of the Lead (proxy for
                             # when the label was added)


def get_not_relevant_label_id(http: httpx.Client) -> str | None:
    """Look up the UUID of the NOT RELEVANT Lead label, or None if absent.

    Returns None when the label doesn't exist (200 + label not in list) — the
    common case before Jordan creates it. Raises on non-200 (auth, network,
    Pipedrive outage) so the operator doesn't misread an API failure as
    "label not found."
    """
    resp = http.get("leadLabels")
    resp.raise_for_status()
    labels = (resp.json().get("data") or [])
    target = NOT_RELEVANT_LABEL_NAME.lower()
    for label in labels:
        if (label.get("name") or "").lower() == target:
            return label.get("id")
    return None


# Pipedrive's v1 paginates Leads via start/limit + an
# additional_data.pagination.more_items_in_collection / next_start cursor.
# 500 is the v1 max page size.
_LEADS_PAGE_SIZE = 500


def list_flagged_leads(
    http: httpx.Client, label_id: str, article_url_field_key: str,
) -> list[FlaggedLead]:
    """Fetch all Leads currently tagged with the given label, across all pages.

    Pipedrive's v1 `/leads?label_id=...` filter is silently ignored — the
    endpoint returns ALL leads regardless. We post-filter by checking each
    lead's `label_ids` array against the target id. (Verified 2026-05-22:
    filter param has no effect.)

    Paginates until `additional_data.pagination.more_items_in_collection` is
    false. Necessary because the underlying /leads endpoint returns *all*
    leads (not just label-matching ones), so once Jordan's pipeline reaches
    the first page boundary, earlier flags would silently drop without this.
    """
    flagged: list[dict] = []
    start = 0
    while True:
        resp = http.get("leads", params={"limit": _LEADS_PAGE_SIZE, "start": start})
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or []  # Pipedrive returns null when empty
        flagged.extend(d for d in data if label_id in (d.get("label_ids") or []))
        pagination = (body.get("additional_data") or {}).get("pagination") or {}
        if not pagination.get("more_items_in_collection"):
            break
        start = pagination.get("next_start")
        if start is None:  # defensive — shouldn't happen if more_items_in_collection is True
            break
    return [_to_flagged(d, article_url_field_key) for d in flagged]


def _to_flagged(d: dict[str, Any], article_url_field_key: str) -> FlaggedLead:
    return FlaggedLead(
        lead_id=str(d.get("id", "")),
        title=str(d.get("title") or ""),
        article_url=d.get(article_url_field_key),
        flagged_at=str(d.get("update_time") or ""),
    )
