"""`python -m pipeline.cli.cache_write <org_name> <source>` — write a Lead to cache.

Stdin: Lead JSON (dict matching pipeline.enrich.Lead's dataclass fields).
Args:  org_name (will be normalized in db._normalize_org_name), source
       (e.g. 'grok', 'apollo', 'in_article' — free text, used for provenance).

Replaces an inline Python heredoc in skill/aether_daily_routine.md that
broke on company names containing double-quotes (shell variable interpolation
into Python source). CLI args avoid the quoting issue entirely.
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
