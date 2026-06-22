"""Run a Phase 2 pattern module and emit an artifact envelope."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.contracts import ArtifactEnvelope
from pipeline.patterns import get_pattern_module
from pipeline.spec import LeadPatternType, load_campaign_spec_v2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="JSON list or artifact envelope containing records.")
    parser.add_argument(
        "--campaign",
        default=None,
        help="Campaign id or YAML path. Defaults to the cleaning campaign.",
    )
    parser.add_argument(
        "--pattern",
        default=None,
        help="Pattern type. Defaults to the campaign lead_pattern.type.",
    )
    parser.add_argument("--run-id", default="adhoc", help="Run id for the output envelope.")
    args = parser.parse_args([] if argv is None else argv)

    spec = load_campaign_spec_v2(args.campaign)
    pattern_type: LeadPatternType = args.pattern or spec.lead_pattern.type
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    records = payload.get("records", payload) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        parser.error("input must be a JSON list or an artifact envelope with records")

    result = get_pattern_module(pattern_type).run(records, spec)
    envelope = ArtifactEnvelope(
        campaign_id=spec.campaign_id,
        run_id=args.run_id,
        stage="pattern",
        records=[record.model_dump(mode="json") for record in result.records],
        metadata={
            "pattern_type": result.pattern_type,
            "stats": result.stats,
        },
    )
    json.dump(envelope.model_dump(mode="json"), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
