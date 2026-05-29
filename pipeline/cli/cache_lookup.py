"""`python -m pipeline.cli.cache_lookup <org_name>` — print cached Lead JSON or null.

Called by the daily routine before any external enrichment. Cache hit means
skip Grok/Apollo entirely for this article.
"""
from __future__ import annotations

import dataclasses
import json
import sys

from pipeline import db


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python -m pipeline.cli.cache_lookup <org_name>\n")
        return 2
    org_name = sys.argv[1]
    conn = db.connect()
    try:
        lead = db.get_cached_enrichment(conn, org_name)
    finally:
        conn.close()
    if lead is None:
        sys.stdout.write("null\n")
    else:
        json.dump(dataclasses.asdict(lead), sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
