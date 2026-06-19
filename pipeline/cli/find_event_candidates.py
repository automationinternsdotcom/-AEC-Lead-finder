"""`python -m pipeline.cli.find_event_candidates` (stdin JSON) — read-only.

Input:  an extracted-article object (needs at least `title`).
Output: JSON array of recent Leads that may describe the SAME news event, for
        Claude to judge. Shape:
        [{"lead_id","title","url","contacts":[...],"score":0.0-1.0}, ...]

Fails OPEN: any Pipedrive error prints `[]` and exits 0, so a transient outage
degrades to today's behavior (create the Lead) — never to a wrong merge.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

from pipeline import config, dedup, email_digest, util


def main() -> int:
    article = json.load(sys.stdin)
    title = article.get("title") or ""
    settings = config.settings()
    lead_fields = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )
    since = datetime.now(timezone.utc) - timedelta(days=settings.dedup_window_days)

    try:
        with email_digest.make_pipedrive_client(settings) as http:
            raw = email_digest.list_raw_leads_since(http, settings, since)
    except Exception as e:  # fail open — see module docstring
        util.log_event("candidate_lookup_failed", error=repr(e))
        json.dump([], sys.stdout)
        sys.stdout.write("\n")
        return 0

    out = []
    for lead in raw:
        score = dedup.same_event_score(title, lead.get("title") or "")
        if score >= settings.dedup_score_threshold:
            rec = dedup.lead_record_from_dict(
                lead, article_url_field=settings.pipedrive_field_article_url,
                lead_fields=lead_fields,
            )
            out.append({"lead_id": rec.lead_id, "title": rec.title,
                        "url": rec.url, "contacts": rec.contacts,
                        "score": round(score, 3)})
    out.sort(key=lambda c: c["score"], reverse=True)
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
