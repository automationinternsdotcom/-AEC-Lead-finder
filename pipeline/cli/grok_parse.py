"""`python -m pipeline.cli.grok_parse [--all]` (stdin Grok text) — print Lead JSON.

Default: prints the first qualifying Lead as a JSON object (or `null`).
`--all`: prints a JSON array of up to 3 Leads (`[]` if none). Each entry has:
  - all Lead fields (name, title, email, phone, linkedin_url, seniority, apollo_id)
  - `is_generic`: true when the parsed name is a job-title placeholder rather
    than a specific person (e.g. "Property Manager"). Triggers Expert retry.
  - `is_high_confidence`: true when both verified email AND phone are present.
    Triggers Expert retry when any entry lacks this — the upgrade target is
    direct-dial phones + non-hedged emails.
"""
from __future__ import annotations

import dataclasses
import json
import sys

from pipeline import grok_parse


def main() -> int:
    args = sys.argv[1:]
    text = sys.stdin.read()

    if "--all" in args:
        # parse_grok_response_blocks returns (lead, raw_block) so we can run
        # is_high_confidence_contact, which checks the raw text for "Likely"
        # hedging that the parsed value alone can't see.
        pairs = grok_parse.parse_grok_response_blocks(text, max_leads=3)
        out = [
            {**dataclasses.asdict(lead),
             "is_generic": grok_parse.is_lead_generic(lead),
             "is_high_confidence": grok_parse.is_high_confidence_contact(lead, block)}
            for lead, block in pairs
        ]
        json.dump(out, sys.stdout)
        sys.stdout.write("\n")
        return 0

    lead = grok_parse.parse_grok_response(text)
    if lead is None:
        sys.stdout.write("null\n")
    else:
        json.dump(dataclasses.asdict(lead), sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
