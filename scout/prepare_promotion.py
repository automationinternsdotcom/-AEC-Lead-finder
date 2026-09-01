"""Derive deterministic promotion inputs from one completed comparison day."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from v2.promotion import collect_promotion_inputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-leads", type=Path, required=True)
    parser.add_argument("--v2-leads", type=Path, required=True)
    parser.add_argument("--v2-uncertain", type=Path, required=True)
    parser.add_argument("--v2-contacts", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--sent-message-count", type=int, required=True)
    parser.add_argument("--resume-verified", action="store_true")
    parser.add_argument("--duplicate-apollo-attempts", type=int, default=0)
    parser.add_argument("--database-corruption", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = collect_promotion_inputs(
        v1_leads_csv=args.v1_leads,
        v2_leads_csv=args.v2_leads,
        v2_uncertain_csv=args.v2_uncertain,
        v2_contacts_csv=args.v2_contacts,
        comparison_manifests=args.manifest,
        sent_message_count=args.sent_message_count,
        resume_verified=args.resume_verified,
        duplicate_apollo_attempts=args.duplicate_apollo_attempts,
        database_corruption=args.database_corruption,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_name(args.output.name + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        file.write(json.dumps(asdict(inputs), indent=2, sort_keys=True) + "\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
