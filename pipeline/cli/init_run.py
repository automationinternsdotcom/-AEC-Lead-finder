"""Initialize a Phase 2 run directory from a campaign spec."""
from __future__ import annotations

import argparse
import sys

from pipeline.run_state import init_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--campaign",
        default=None,
        help="Campaign id or YAML path. Defaults to the cleaning campaign.",
    )
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id for tests/manual runs.")
    args = parser.parse_args([] if argv is None else argv)

    run_dir = init_run(args.campaign, run_id=args.run_id)
    sys.stdout.write(str(run_dir) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
