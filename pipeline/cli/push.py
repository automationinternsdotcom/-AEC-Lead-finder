"""`python -m pipeline.cli.push` (stdin JSON) — create Pipedrive deal, print result.

Input:  {"article": <ExtractedArticle>, "lead": <Lead | null>, "url": "..."}
Output: {"deal_id": <int>, "org_id": <int | null>,
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
    url = raw["url"]

    settings = config.settings()
    rates = config.load_rates()
    est_value, basis = extract.estimate_deal_size(article, rates)

    org_id, person_id, deal_id = push.sync_to_pipedrive(
        article, lead, est_value, basis, url, settings,
    )
    skipped = org_id is None  # sync returns (None, None, existing_id) on dedup hit
    json.dump({
        "deal_id": deal_id, "org_id": org_id,
        "person_id": person_id, "skipped": skipped,
    }, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
