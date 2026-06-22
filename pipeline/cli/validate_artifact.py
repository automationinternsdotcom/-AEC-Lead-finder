"""Validate a Phase 2 artifact envelope JSON file."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.contracts import load_artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", help="Path to an artifact envelope JSON file.")
    args = parser.parse_args([] if argv is None else argv)

    artifact = load_artifact(Path(args.artifact))
    json.dump(
        {
            "ok": True,
            "campaign_id": artifact.campaign_id,
            "run_id": artifact.run_id,
            "stage": artifact.stage,
            "record_count": len(artifact.records),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
