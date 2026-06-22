"""Parse an enrichment transcript and emit an artifact envelope."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.spec import load_campaign_spec_v2
from pipeline.transcript_parser import (
    enrichment_result_to_artifact,
    parse_grok_enrichment_transcript,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", help="Path to a saved browser/chat transcript.")
    parser.add_argument("--campaign", default=None)
    parser.add_argument("--run-id", default="adhoc")
    parser.add_argument("--company-name", default=None)
    parser.add_argument("--provider", choices=("grok",), default="grok")
    parser.add_argument("--mode", default=None)
    args = parser.parse_args([] if argv is None else argv)

    spec = load_campaign_spec_v2(args.campaign)
    text = Path(args.transcript).read_text(encoding="utf-8")
    result = parse_grok_enrichment_transcript(
        text,
        company_name=args.company_name,
        mode=args.mode,
    )
    artifact = enrichment_result_to_artifact(
        result,
        campaign_id=spec.campaign_id,
        run_id=args.run_id,
    )
    json.dump(artifact.model_dump(mode="json"), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
