# Cleanup Archive — 2026-06-23

This branch preserves files selected for cleanup from `main`.

## Tracked legacy files preserved by this branch

These files remain present on this archive branch even if they are removed from `main`:

- `guarded_push.py`
- `skill/aether_daily_routine.md`
- `skill/SKILLS_Scheduled.md`
- `skill/fetch_feeds.py`
- `skill/setup_sheets.py`
- `skill/aether_day1_engineering.md`
- `skill/aether_engineering_plan.md`
- `skill/aether_week1_implementation.md`

## Runtime artifact bundles

- `runtime-artifacts.tar.gz`
  - `logs/`
  - `db.sqlite.bak-20260615-154939`
  - `db.sqlite.bak-20260622-144353-pre-june22-cleanup`
  - `leads-backup-since-20260529.json`
  - `.pytest_cache/`
  - `__pycache__/`
  - `pipeline/__pycache__/`
  - `pipeline/cli/__pycache__/`
  - `tests/__pycache__/`

- `venv.tar.gz`
  - `.venv/`

The current runtime database `db.sqlite` is intentionally not archived here because it
is active pipeline state, not a deletion candidate.
