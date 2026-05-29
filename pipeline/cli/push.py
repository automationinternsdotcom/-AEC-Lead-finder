"""`python -m pipeline.cli.push` (stdin JSON) — create Pipedrive Lead, print result.

Input:  {
  "article": <ExtractedArticle>,
  "lead": <Lead | null>,
  "url": "...",
  "extra_contacts": [<Lead>, <Lead>]   # optional — fills Lead 2 / Lead 3 fields
}
Output: {"lead_id": <uuid-str>, "org_id": <int | null>,
         "person_id": <int | null>, "skipped": <bool>}
"""
from __future__ import annotations

import json
import sys

from pipeline import config, enrich, extract, push
from schema import ExtractedArticle


def main() -> int:
    raw = json.load(sys.stdin)
    article = ExtractedArticle.model_validate(raw["article"])
    lead_dict = raw.get("lead")
    lead = enrich.Lead(**lead_dict) if lead_dict else None
    extra_raw = raw.get("extra_contacts") or []
    extras = [enrich.Lead(**c) for c in extra_raw] if extra_raw else None
    url = raw["url"]

    settings = config.settings()
    rates = config.load_rates()
    est_value, basis = extract.estimate_deal_size(article, rates)

    org_id, person_id, lead_id = push.sync_to_pipedrive(
        article, lead, est_value, basis, url, settings,
        extra_contacts=extras,
    )
    skipped = org_id is None  # sync returns (None, None, existing_uuid) on dedup hit
    json.dump({
        "lead_id": lead_id, "org_id": org_id,
        "person_id": person_id, "skipped": skipped,
    }, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
