"""Pipedrive cleanup, schema v2, and pipeline v2 planning helpers.

Pure functions only: these produce dry-run plans/reports that can be shown in
the Friday demo before any production Pipedrive mutation.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


SCHEMA_V2_FIELDS = (
    {"name": "Article URL", "field_type": "varchar", "required": True},
    {"name": "Date Posted", "field_type": "date", "required": True},
    {"name": "Lead 1", "field_type": "varchar", "required": True},
    {"name": "Lead 1 LinkedIn", "field_type": "varchar", "required": True},
    {"name": "Lead 2", "field_type": "varchar", "required": False},
    {"name": "Lead 2 LinkedIn", "field_type": "varchar", "required": False},
    {"name": "Lead 3", "field_type": "varchar", "required": False},
    {"name": "Lead 3 LinkedIn", "field_type": "varchar", "required": False},
    {"name": "Enrichment Source", "field_type": "varchar", "required": True},
    {"name": "Enrichment Mode", "field_type": "varchar", "required": True},
    {"name": "Next Follow-up Due", "field_type": "date", "required": True},
    {"name": "Follow-up Status", "field_type": "enum", "required": True},
    {"name": "Automation Log", "field_type": "text", "required": False},
)

PIPELINE_V2_NAME = "Aether Lead Review"
PIPELINE_V2_STAGES = (
    "AI Scored",
    "Enriched",
    "Jordan Review",
    "Follow-up Scheduled",
    "Contacted",
    "Qualified Opportunity",
)


def schema_plan(existing_fields: Iterable[dict]) -> list[dict]:
    existing_by_name = {
        (f.get("name") or "").strip().lower(): f for f in existing_fields
    }
    plan = []
    for wanted in SCHEMA_V2_FIELDS:
        found = existing_by_name.get(wanted["name"].lower())
        if found is None:
            plan.append({**wanted, "action": "create"})
            continue
        found_type = found.get("field_type") or found.get("type")
        action = "ok" if not found_type or found_type == wanted["field_type"] else "review_type"
        plan.append({**wanted, "action": action, "key": found.get("key")})
    return plan


def cleanup_report(leads: Iterable[dict]) -> dict:
    rows = list(leads)
    by_url: dict[str, list[str]] = defaultdict(list)
    missing = []
    normalized_labels = {}
    for row in rows:
        lead_id = str(row.get("id") or "")
        url = (row.get("article_url") or "").strip()
        if url:
            by_url[url].append(lead_id)
        else:
            missing.append(lead_id)
        labels = row.get("labels") or []
        normalized_labels[lead_id] = sorted({_normalize_label(l) for l in labels if str(l).strip()})
    duplicates = [
        {"article_url": url, "lead_ids": ids}
        for url, ids in sorted(by_url.items())
        if len(ids) > 1
    ]
    return {
        "total": len(rows),
        "missing_article_url_ids": missing,
        "duplicate_article_url_groups": duplicates,
        "normalized_labels": normalized_labels,
    }


def pipeline_plan(existing_pipelines: Iterable[dict], existing_stages: Iterable[dict]) -> dict:
    existing_pipeline = next(
        (p for p in existing_pipelines if (p.get("name") or "").strip() == PIPELINE_V2_NAME),
        None,
    )
    pipeline_action = "ok" if existing_pipeline else "create"
    existing_stage_names = {
        (s.get("name") or "").strip()
        for s in existing_stages
        if not existing_pipeline or s.get("pipeline_id") in {existing_pipeline.get("id"), str(existing_pipeline.get("id"))}
    }
    return {
        "pipeline": {
            "name": PIPELINE_V2_NAME,
            "action": pipeline_action,
            "id": existing_pipeline.get("id") if existing_pipeline else None,
        },
        "stages": [
            {
                "name": name,
                "order_nr": i + 1,
                "action": "ok" if name in existing_stage_names else "create",
            }
            for i, name in enumerate(PIPELINE_V2_STAGES)
        ],
    }


def _normalize_label(label: str) -> str:
    return " ".join(str(label).strip().upper().split())
