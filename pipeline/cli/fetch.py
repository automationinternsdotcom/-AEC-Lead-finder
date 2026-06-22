"""`python -m pipeline.cli.fetch` — print JSON array of new + backlog URLs.

Output shape: [{"url_hash": "...", "url": "...", "source": "...", "title": "..."}, ...]
"""
from __future__ import annotations

import argparse
import json
import sys

from pipeline import db, fetch
from pipeline.spec import load_campaign_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--campaign",
        default=None,
        help="Campaign id or YAML path. Defaults to the cleaning campaign.",
    )
    parser.add_argument(
        "--no-campaign",
        action="store_true",
        help="Legacy mode: read enabled rows from sources.yaml directly.",
    )
    args = parser.parse_args([] if argv is None else argv)

    spec = None if args.no_campaign else load_campaign_spec(args.campaign)
    conn = db.connect()
    try:
        backlog_rows = db.get_unprocessed_urls(conn)
        fresh = fetch.discover_new_urls(conn, spec)
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
    raise SystemExit(main(sys.argv[1:]))
