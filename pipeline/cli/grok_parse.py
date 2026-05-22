"""`python -m pipeline.cli.grok_parse` (stdin Grok text) — print Lead JSON or `null`.

Used by the daily routine's enrichment step: the Grok response captured
via Claude-in-Chrome is piped through this CLI, which produces a Lead
JSON object that can flow straight into `pipeline.cli.push`.
"""
from __future__ import annotations

import dataclasses
import json
import sys

from pipeline import grok_parse


def main() -> int:
    text = sys.stdin.read()
    lead = grok_parse.parse_grok_response(text)
    if lead is None:
        sys.stdout.write("null\n")
    else:
        json.dump(dataclasses.asdict(lead), sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
