"""Register discovered source URLs and print fetch-compatible rows."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline import db
from pipeline.contracts import load_artifact
from pipeline.source_discovery import records_for_fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", help="Path to a discover artifact envelope.")
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Only print rows; do not record discovered URLs in SQLite.",
    )
    args = parser.parse_args([] if argv is None else argv)

    artifact = load_artifact(Path(args.artifact))
    rows = records_for_fetch(artifact)
    if not args.no_db:
        conn = db.connect()
        try:
            fresh = []
            for row in rows:
                if db.record_seen(conn, row["url_hash"], row["url"], row["source"], row["title"]):
                    fresh.append(row)
            conn.commit()
            rows = fresh
        finally:
            conn.close()
    json.dump(rows, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
