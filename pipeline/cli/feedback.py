"""`python -m pipeline.cli.feedback` — print JSON array of NOT-RELEVANT-flagged Leads.

Output shape: [{"lead_id": "...", "title": "...", "article_url": "...",
                "flagged_at": "..."}, ...]

Called by the daily routine's run-report step. Operator scans output to
manually tune skill/aether_daily_routine.md Step 2b when patterns emerge.

Exit codes:
  0  ok (array may be empty)
  3  NOT RELEVANT label doesn't exist in Pipedrive yet — operator should
     create it in Settings -> Lead labels
"""
from __future__ import annotations

import dataclasses
import json
import sys

import httpx

from pipeline import config, feedback


def main() -> int:
    settings = config.settings()
    with httpx.Client(
        base_url=f"https://{settings.pipedrive_domain}.pipedrive.com/api/v1/",
        params={"api_token": settings.pipedrive_api_token},
        timeout=config.HTTP_TIMEOUT_SEC,
    ) as http:
        label_id = feedback.get_not_relevant_label_id(http)
        if label_id is None:
            sys.stderr.write(
                f"Label '{feedback.NOT_RELEVANT_LABEL_NAME}' not found in Pipedrive. "
                "Create it in Settings -> Lead labels first.\n"
            )
            sys.stdout.write("[]\n")  # still write valid empty array so callers can jq it
            return 3

        flagged = feedback.list_flagged_leads(
            http, label_id, settings.pipedrive_field_article_url,
        )

    json.dump([dataclasses.asdict(f) for f in flagged], sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
