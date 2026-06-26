"""`python -m pipeline.cli.cache_write <org_name> <source>` — write a Lead to cache.

Stdin: Lead JSON (dict matching pipeline.enrich.Lead's dataclass fields).
Args:  org_name (will be normalized in db._normalize_org_name), source
       (e.g. 'grok', 'apollo', 'in_article' — free text, used for provenance).

Keeps cache writes as a real CLI so company names with shell-sensitive
characters pass through as arguments instead of interpolated source text.
"""
from __future__ import annotations

import json
import sys

from pipeline import db
from pipeline.enrich import Lead


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: python -m pipeline.cli.cache_write <org_name> <source>\n")
        return 2

    org_name, source = sys.argv[1], sys.argv[2]

    try:
        lead_dict = json.load(sys.stdin)
        lead = Lead(**lead_dict)
    except (json.JSONDecodeError, TypeError) as e:
        sys.stderr.write(f"invalid_lead_json: {e}\n")
        return 2

    conn = db.connect()
    try:
        db.cache_enrichment(conn, org_name, lead, source=source)
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
