"""`python -m pipeline.cli.fetch` — print JSON array of new + backlog URLs.

Output shape: [{"url_hash": "...", "url": "...", "source": "...", "title": "..."}, ...]
"""
from __future__ import annotations

import json
import sys

from pipeline import db, fetch


def main() -> int:
    conn = db.connect()
    try:
        backlog_rows = db.get_unprocessed_urls(conn)
        fresh = fetch.discover_new_urls(conn)
        conn.commit()

        urls = [
            {"url_hash": r["url_hash"], "url": r["url"],
             "source": r["source"], "title": r["title"] or ""}
            for r in backlog_rows
        ] + [
            {"url_hash": a.url_hash, "url": a.url,
             "source": a.source, "title": a.title}
            for a in fresh
        ]
        json.dump(urls, sys.stdout)
        sys.stdout.write("\n")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
