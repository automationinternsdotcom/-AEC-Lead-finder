"""Parse a Gemini source-discovery transcript into a discover artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.source_discovery import (
    gemini_discovery_to_artifact,
    parse_gemini_discovery_transcript,
)
from pipeline.spec import load_campaign_spec_v2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", help="Path to a saved Gemini discovery transcript.")
    parser.add_argument("--campaign", default=None)
    parser.add_argument("--run-id", default="adhoc")
    parser.add_argument("--min-confidence", type=float, default=None)
    args = parser.parse_args([] if argv is None else argv)

    spec = load_campaign_spec_v2(args.campaign)
    text = Path(args.transcript).read_text(encoding="utf-8")
    result = parse_gemini_discovery_transcript(
        text,
        spec,
        min_confidence=args.min_confidence,
    )
    artifact = gemini_discovery_to_artifact(
        result,
        campaign_id=spec.campaign_id,
        run_id=args.run_id,
    )
    json.dump(artifact.model_dump(mode="json"), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
