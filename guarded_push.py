#!/usr/bin/env python3
"""Guarded LIVE push with a hard cap. Only invoked AFTER the nightly run has
validated the bridge/fetch/qualify/enrich phases.

Usage:  GUARD_MAX_PUSH=20 .venv/bin/python guarded_push.py <leads.json>

<leads.json> = JSON array of push payloads: {"article": {...}, "lead": <Lead|null>,
"extra_contacts": [...], "url": "..."}  (already qualified + enriched).

Forces DRY_RUN=0 for its push subprocesses ONLY (deliberate, contained live
write — the surrounding env stays DRY_RUN=1 so nothing else writes).

GUARD_MAX_PUSH caps the number of NEW leads created (dedup-skips don't count).
Unset or 0 = UNLIMITED (push all). Set a positive integer to cap.
"""
import sys, os, json, subprocess

RUNNER = "/Users/openclaw/aether-runner"
PY = f"{RUNNER}/.venv/bin/python"
MAX = int(os.environ.get("GUARD_MAX_PUSH", "0"))  # 0 or unset = UNLIMITED (no cap)
UNLIMITED = MAX <= 0
CAP_LABEL = "∞" if UNLIMITED else str(MAX)

leads = json.load(open(sys.argv[1]))
env = dict(os.environ)
env["DRY_RUN"] = "0"  # LIVE — contained to these push subprocesses only.

created = skipped = errors = 0
for item in leads:
    if not UNLIMITED and created >= MAX:
        print(f"CAP REACHED: {MAX} new leads created — stopping (had {len(leads)} candidates).")
        break
    co = (item.get("article") or {}).get("company_name", "?")
    r = subprocess.run([PY, "-m", "pipeline.cli.push"], input=json.dumps(item),
                       capture_output=True, text=True, cwd=RUNNER, env=env)
    if r.returncode == 0:
        out = json.loads(r.stdout or "{}")
        if out.get("skipped"):
            skipped += 1
            print(f"  skip(dedup) {co}")
        else:
            created += 1
            print(f"  CREATED [{created}/{CAP_LABEL}] {co} -> {out.get('lead_id')}")
    else:
        errors += 1
        print(f"  ERROR {co}: {(r.stderr.strip().splitlines() or [''])[-1][:90]}")

print(f"GUARDED PUSH DONE: created={created} skipped={skipped} errors={errors} cap={CAP_LABEL}")
