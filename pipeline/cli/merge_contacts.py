"""`python -m pipeline.cli.merge_contacts` (stdin JSON) — merge contacts into a
keeper Lead. NEVER deletes.

Input: {
  "keeper_lead_id": "uuid",
  "contacts": ["Name | Title | Email | Phone", ...],  # contacts to ADD
  "merged_url": "https://..."   # optional: mark its url_hash 'merged' + breadcrumb
}
Output: {"keeper_lead_id","written":{<field>:<str>},"overflow":[...],"dry_run":bool}

Only the Lead 1/2/3 custom fields are touched (contacts only). Title, notes,
value, Article URL, and the linked Person are left exactly as the keeper's.
Honors DRY_RUN (logs, no writes).
"""
from __future__ import annotations

import json
import sys

from pipeline import config, db, dedup, push, util


def main() -> int:
    raw = json.load(sys.stdin)
    keeper_id = raw["keeper_lead_id"]
    incoming = [str(c) for c in (raw.get("contacts") or [])]
    merged_url = raw.get("merged_url")

    settings = config.settings()
    lead_fields = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )

    with push.PipedriveClient(settings) as pd:
        keeper = pd.get("leads", keeper_id)
        existing = [str(c) for f in lead_fields if f and (c := dedup.cf(keeper, f))]
        result = dedup.merge_contact_strings(existing, incoming)
        if result.overflow:
            util.log_event("merge_contacts_overflow", keeper=keeper_id,
                           dropped=len(result.overflow))

        # Map the kept contacts back onto Lead 1/2/3 in order.
        written = {f: val for f, val in zip(lead_fields, result.kept) if f}

        if settings.dry_run:
            util.log_event("dry_run_merge_contacts", keeper=keeper_id,
                           written=len(written), merged_url=merged_url)
            json.dump({"keeper_lead_id": keeper_id, "written": written,
                       "overflow": result.overflow, "dry_run": True}, sys.stdout)
            sys.stdout.write("\n")
            return 0

        if written:
            pd.patch("leads", keeper_id, written)
        if merged_url:
            pd.post("notes", {"lead_id": keeper_id,
                              "content": f"merged via event-dedup: {merged_url}"})
        if result.overflow:
            pd.post("notes", {"lead_id": keeper_id,
                              "content": dedup.overflow_note_body(result.overflow)})

    # Order matters: we PATCH the keeper's contacts FIRST, then mark the URL
    # 'merged'. If the mark fails after a successful PATCH, the worst case is a
    # re-fetch re-encountering the URL — which the find_event_candidates gate
    # catches again next run. Marking 'merged' FIRST would risk the opposite:
    # the URL suppressed but contacts never written = a silently lost lead.
    # Any post-PATCH failure propagates (non-zero exit) so the caller can retry.
    if merged_url:
        # Mark the merged-away URL so a future fetch never re-creates it.
        conn = db.connect()
        try:
            db.mark_seen_status(conn, util.sha256_hex(util.canonicalize_url(merged_url)), "merged")
            conn.commit()
        finally:
            conn.close()

    util.log_event("merge_contacts_done", keeper=keeper_id, written=len(written))
    json.dump({"keeper_lead_id": keeper_id, "written": written,
               "overflow": result.overflow, "dry_run": False}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
