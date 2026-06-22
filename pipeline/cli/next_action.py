"""Print the next action for a Phase 2 run manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.run_state import next_action_for_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", help="Path to runs/<campaign>/<run_id>.")
    args = parser.parse_args([] if argv is None else argv)

    action = next_action_for_run(Path(args.run_dir))
    json.dump(action.model_dump(mode="json"), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
