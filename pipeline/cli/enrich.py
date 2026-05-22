"""`python -m pipeline.cli.enrich <domain>` — Apollo lookup, print Lead JSON or `null`.

Output: JSON object with {name, title, email, phone, linkedin_url, seniority, apollo_id}
        or literal `null` if no lead found / Apollo not configured.
"""
from __future__ import annotations

import dataclasses
import json
import sys

from pipeline import config, enrich


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python -m pipeline.cli.enrich <domain>\n")
        return 2

    domain = sys.argv[1]
    try:
        settings = config.settings()
    except RuntimeError:
        settings = None  # No env config — find_lead will return None gracefully
    lead = enrich.find_lead(domain, settings)
    if lead is None:
        sys.stdout.write("null\n")
    else:
        json.dump(dataclasses.asdict(lead), sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
