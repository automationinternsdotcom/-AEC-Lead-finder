"""`python -m pipeline.cli.backfill [--days 60]` — run historical source sweep."""
from __future__ import annotations

import argparse
import json
import sys

from pipeline import backfill, db


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()

    conn = db.connect()
    try:
        articles = backfill.discover_backfill_urls(conn, days=args.days)
        conn.commit()
        json.dump([
            {
                "url_hash": a.url_hash,
                "url": a.url,
                "source": a.source,
                "title": a.title,
                "published_at": a.published_at.isoformat() if a.published_at else None,
            }
            for a in articles
        ], sys.stdout)
        sys.stdout.write("\n")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
