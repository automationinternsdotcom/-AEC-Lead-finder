"""Create a human-reviewable destination preview from an artifact envelope."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.contracts import load_artifact
from pipeline.delivery import delivery_records_from_artifact
from pipeline.destinations import destination_for
from pipeline.run_state import RUNS_DIR
from pipeline.spec import load_campaign_spec_v2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", help="Path to an artifact envelope.")
    parser.add_argument("--campaign", default=None)
    parser.add_argument("--destination", default="excel")
    parser.add_argument("--run-id", default="adhoc")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args([] if argv is None else argv)

    spec = load_campaign_spec_v2(args.campaign)
    destination_config = next(
        (d for d in spec.destinations if d.type == args.destination),
        None,
    )
    if destination_config is None:
        parser.error(f"campaign has no {args.destination!r} destination")

    artifact = load_artifact(Path(args.artifact))
    records = delivery_records_from_artifact(artifact)
    output_dir = Path(args.output_dir) if args.output_dir else RUNS_DIR / spec.campaign_id / args.run_id / "previews"
    preview = destination_for(destination_config).preview(
        records,
        output_dir=output_dir,
        run_id=args.run_id,
    )
    json.dump(preview.model_dump(mode="json"), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
