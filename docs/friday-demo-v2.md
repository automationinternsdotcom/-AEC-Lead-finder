# Friday Demo V2 Implementation Notes

Date: 2026-06-04

## What Changed

- Added `pipeline.cli.backfill --days 60` for an expanded two-month Google News sweep.
- Added LinkedIn preservation in Pipedrive-visible Lead 1/2/3 contact text.
- Added optional individual `Lead 1 LinkedIn`, `Lead 2 LinkedIn`, `Lead 3 LinkedIn` custom-field writes.
- Added `pipeline.cli.pipedrive_v2` dry-run commands for:
  - schema v2 plan
  - pipeline v2 plan
  - cleanup/normalization report
  - follow-up automation preview
- Added optional post-create Pipedrive v2 activities for follow-up call and email reminder, gated by `PIPEDRIVE_ENABLE_AUTOMATIONS=1`.

## Demo Commands

```bash
uv run python -m pipeline.cli.backfill --days 60 > /tmp/aether_backfill60.json
jq length /tmp/aether_backfill60.json

uv run python -m pipeline.cli.pipedrive_v2 schema | jq .
uv run python -m pipeline.cli.pipedrive_v2 pipeline | jq .
uv run python -m pipeline.cli.pipedrive_v2 cleanup < /tmp/leads.json | jq .
uv run python -m pipeline.cli.pipedrive_v2 automation-preview --input /tmp/aether_automation_input.json | jq .
```

## Verification Evidence

- Unit test suite: `uv run python -m unittest discover tests -v`
- Result on 2026-06-04: 197 tests passed.
- Backfill run on 2026-06-04: 226 new candidate URLs from ten 60-day queries.
- Dry-run schema plan generated 13 schema v2 fields.
- Dry-run pipeline plan generated `Aether Lead Review` plus six stages.
- Dry-run cleanup report detected duplicate Article URLs, missing Article URLs, and normalized labels.
- Dry-run automation preview generated call and email reminder payloads for the next business day.

## Remaining Production Gates

- Confirm Jordan's Pipedrive custom-field hashes and owner/user ID.
- Keep `PIPEDRIVE_ENABLE_AUTOMATIONS=0` until Jon/Jordan approve actual activity creation.
- Run cleanup/schema/pipeline commands against Jordan's real Pipedrive exports or API output before any mutation.
- Process backfill results in batches because broad 60-day Google News queries intentionally include some noise for recall.
