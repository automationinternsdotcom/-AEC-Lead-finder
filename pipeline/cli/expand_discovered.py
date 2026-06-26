"""Classify/expand discovered sources into fetch-ready rows."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline import db
from pipeline.contracts import load_artifact
from pipeline.source_expansion import expand_discovery_artifact
from pipeline.spec import load_campaign_spec_v2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", help="Path to a discover artifact envelope.")
    parser.add_argument(
        "--campaign",
        default=None,
        help="Campaign id or YAML path. Defaults to the artifact campaign/default campaign.",
    )
    parser.add_argument("--run-id", default=None, help="Run id for output paths. Defaults to artifact.run_id.")
    parser.add_argument("--run-dir", default=None, help="Run directory for writing artifacts.")
    parser.add_argument("--no-db", action="store_true", help="Do not record final fetch rows in SQLite.")
    parser.add_argument("--max-entries-per-source", type=int, default=25)
    args = parser.parse_args([] if argv is None else argv)

    artifact_path = Path(args.artifact)
    artifact = load_artifact(artifact_path)
    spec = load_campaign_spec_v2(args.campaign or artifact.campaign_id)
    run_id = args.run_id or artifact.run_id
    run_dir = Path(args.run_dir) if args.run_dir else artifact_path.parent.parent
    dedupe_namespace = spec.sources.dedupe.namespace or (
        spec.campaign_id if spec.sources.dedupe.scope == "campaign" else "global"
    )

    rows, classified = expand_discovery_artifact(
        artifact,
        dedupe_namespace=dedupe_namespace,
        max_entries_per_source=args.max_entries_per_source,
    )
    row_dicts = [row.to_dict() for row in rows]

    if not args.no_db:
        conn = db.connect()
        try:
            fresh = []
            for row in row_dicts:
                if db.record_seen(conn, row["url_hash"], row["url"], row["source"], row.get("title")):
                    fresh.append(row)
            conn.commit()
            row_dicts = fresh
        finally:
            conn.close()

    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "classified_sources.json").write_text(
        json.dumps(
            {
                "campaign_id": spec.campaign_id,
                "run_id": run_id,
                "dedupe_namespace": dedupe_namespace,
                "records": [item.to_dict() for item in classified],
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    (artifacts_dir / "fetch_rows.json").write_text(
        json.dumps(row_dicts, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(json.dumps(row_dicts, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
