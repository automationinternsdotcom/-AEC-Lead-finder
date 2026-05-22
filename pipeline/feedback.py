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

    Returns None (rather than raising) when the label doesn't exist — that
    just means Jordan hasn't created it yet. The caller surfaces a friendly
    'create the label first' message instead of crashing.
    """
    resp = http.get("leadLabels")
    if resp.status_code != 200:
        return None
    labels = (resp.json().get("data") or [])
    target = NOT_RELEVANT_LABEL_NAME.lower()
    for label in labels:
        if (label.get("name") or "").lower() == target:
            return label.get("id")
    return None


def list_flagged_leads(
    http: httpx.Client, label_id: str, article_url_field_key: str,
) -> list[FlaggedLead]:
    """Fetch all Leads currently tagged with the given label.

    Note: Pipedrive's /leads?label_id=... returns ALL leads with that label
    (not just newly-tagged). The routine should de-dup against its own
    log of previously-surfaced flags if it wants a 'since last run' view —
    this module returns the raw current state.
    """
    resp = http.get("leads", params={"label_id": label_id, "limit": 500})
    if resp.status_code != 200:
        return []
    data = resp.json().get("data") or []  # Pipedrive returns null when empty
    return [_to_flagged(d, article_url_field_key) for d in data]


def _to_flagged(d: dict[str, Any], article_url_field_key: str) -> FlaggedLead:
    return FlaggedLead(
        lead_id=str(d.get("id", "")),
        title=str(d.get("title") or ""),
        article_url=d.get(article_url_field_key),
        flagged_at=str(d.get("update_time") or ""),
    )
