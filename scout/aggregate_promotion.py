"""Apply the two-of-three automatic promotion gate to daily scorecards."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v2.promotion import aggregate_promotion


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scorecards", type=Path, nargs=3)
    args = parser.parse_args(argv)
    cards = [json.loads(path.read_text(encoding="utf-8")) for path in args.scorecards]
    result = aggregate_promotion(cards)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["decision"] == "promote_v2" else 1


if __name__ == "__main__":
    raise SystemExit(main())
