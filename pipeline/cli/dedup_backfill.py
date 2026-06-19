"""`python -m pipeline.cli.dedup_backfill --since YYYY-MM-DD [--apply]`.

DRY-RUN BY DEFAULT. Clusters Leads created on/after `--since` by same-event
title similarity, picks the most-complete keeper per cluster, and computes the
merged contact set. Without --apply it only PRINTS the plan (JSON) for review.
With --apply it executes: merge contacts into the keeper FIRST, then delete the
losers — and deletes nothing for a cluster whose merge failed.

Plan shape:
{
  "summary": {"clusters": N, "leads_deleted": M},
  "clusters": [
    {"keeper_lead_id","keeper_title","delete_lead_ids":[...],"delete_urls":[...],
     "merged_contacts":[...],"overflow":[...]}, ...
  ]
}
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from pipeline import config, dedup, email_digest, push, util


def _build_plan(raw_leads: list[dict], settings) -> dict:
    lead_fields = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )
    recs = [
        dedup.lead_record_from_dict(
            lead, article_url_field=settings.pipedrive_field_article_url,
            lead_fields=lead_fields,
        )
        for lead in raw_leads
    ]
    clusters_out = []
    for cluster in dedup.cluster_leads(recs, settings.dedup_score_threshold):
        if len(cluster) < 2:
            continue
        keeper = max(cluster, key=dedup.completeness_key)
        losers = [r for r in cluster if r.lead_id != keeper.lead_id]
        incoming = [c for r in losers for c in r.contacts]
        merged = dedup.merge_contact_strings(keeper.contacts, incoming)
        clusters_out.append({
            "keeper_lead_id": keeper.lead_id,
            "keeper_title": keeper.title,
            "delete_lead_ids": [r.lead_id for r in losers],
            "delete_urls": [r.url for r in losers],
            "merged_contacts": merged.kept,
            "overflow": merged.overflow,
        })
    deleted = sum(len(c["delete_lead_ids"]) for c in clusters_out)
    return {"summary": {"clusters": len(clusters_out), "leads_deleted": deleted},
            "clusters": clusters_out}


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="pipeline.cli.dedup_backfill")
    p.add_argument("--since", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--apply", action="store_true",
                   help="Execute the plan (merge then delete). Default: dry-run.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        d = date.fromisoformat(args.since)
    except ValueError:
        sys.stderr.write(f"Invalid --since date: {args.since!r}\n")
        return 2
    since = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

    settings = config.settings()
    with email_digest.make_pipedrive_client(settings) as http:
        raw = email_digest.list_raw_leads_since(http, settings, since)
    plan = _build_plan(raw, settings)

    if not args.apply:
        json.dump(plan, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    return _apply(plan, settings)


def _apply(plan: dict, settings) -> int:
    """Merge contacts into each keeper, then delete that cluster's losers.
    A cluster whose merge raises is skipped and its losers are NOT deleted."""
    if settings.dry_run:
        sys.stderr.write("dedup_backfill: --apply with DRY_RUN=1 — simulating, no writes/deletes.\n")
    lead_fields = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )
    deleted = 0
    with push.PipedriveClient(settings) as pd:
        for cl in plan["clusters"]:
            keeper_id = cl["keeper_lead_id"]
            try:
                written = {f: val for f, val in zip(lead_fields, cl["merged_contacts"]) if f}
                if written and not settings.dry_run:
                    pd.patch("leads", keeper_id, written)
                overflow = cl.get("overflow") or []
                if overflow and not settings.dry_run:
                    pd.post("notes", {"lead_id": keeper_id,
                                      "content": dedup.overflow_note_body(overflow)})
            except Exception as e:
                util.log_event("backfill_merge_failed", keeper=keeper_id, error=repr(e))
                continue  # never delete when the merge/preserve step failed
            for lid in cl["delete_lead_ids"]:
                if not settings.dry_run:
                    pd.delete("leads", lid)
                deleted += 1
            util.log_event("backfill_cluster_done", keeper=keeper_id,
                           deleted=len(cl["delete_lead_ids"]), dry_run=settings.dry_run)
    json.dump({"applied": not settings.dry_run, "leads_deleted": deleted,
               "dry_run": settings.dry_run}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
