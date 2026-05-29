"""`python -m pipeline.cli.assessor_lookup <address>` — print Assessor JSON or null.

Used by the daily routine before dispatching the Grok enricher. When the
article has an AZ property address, this returns the legally-recorded
owning entity (often a holding LLC distinct from the operating company),
which gets passed into the Grok query as an owner-hint.
"""
from __future__ import annotations

import json
import sys

from pipeline import assessor, util


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python -m pipeline.cli.assessor_lookup <address>\n")
        return 2
    address = sys.argv[1]
    with util.make_http_client() as http:
        result = assessor.lookup_by_address(address, http)
    if result is None:
        sys.stdout.write("null\n")
    else:
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
