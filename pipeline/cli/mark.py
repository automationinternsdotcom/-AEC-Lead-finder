"""`python -m pipeline.cli.mark <url_hash> <status>` — update seen_urls.status.

Valid statuses: new, extracted, filtered, pushed, failed.
Exit codes: 0 = ok, 2 = bad args.
"""
from __future__ import annotations

import sys

from pipeline import db

VALID_STATUSES = {"new", "extracted", "filtered", "pushed", "failed"}


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: python -m pipeline.cli.mark <url_hash> <status>\n")
        return 2
    url_hash, status = sys.argv[1], sys.argv[2]
    if status not in VALID_STATUSES:
        sys.stderr.write(f"invalid status: {status!r}\n")
        return 2

    conn = db.connect()
    try:
        db.mark_seen_status(conn, url_hash, status)
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
