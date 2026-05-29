"""`python -m pipeline.cli.update` (stdin JSON) — sync improved contacts into
an already-created Pipedrive Lead. Mirrors `pipeline.cli.push` but UPDATES
instead of creating.

Input:  {
  "url": "...",                       # Article URL of the existing Lead
  "lead": <Lead | null>,              # new primary contact (verified-only policy applies)
  "extra_contacts": [<Lead>, <Lead>]  # optional — refresh Lead 2 / Lead 3 text
}
Output: {"lead_id": <uuid|null>, "person_id": <int|null>, "updated": <bool>, ...}

Honors DRY_RUN (logs `dry_run_update`, no writes). Extra keys emitted by
`grok_parse --all` (is_generic / is_high_confidence) are ignored.
"""
from __future__ import annotations

import dataclasses
import json
import sys

from pipeline import config, enrich, push

_LEAD_FIELDS = {f.name for f in dataclasses.fields(enrich.Lead)}


def _to_lead(d: dict | None) -> enrich.Lead | None:
    if not d:
        return None
    return enrich.Lead(**{k: v for k, v in d.items() if k in _LEAD_FIELDS})


def main() -> int:
    raw = json.load(sys.stdin)
    lead = _to_lead(raw.get("lead"))
    extras = [_to_lead(c) for c in (raw.get("extra_contacts") or [])]
    extras = [c for c in extras if c is not None] or None

    result = push.update_lead_contacts(raw["url"], lead, extras, config.settings())
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
