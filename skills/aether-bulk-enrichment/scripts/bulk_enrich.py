#!/usr/bin/env python3
"""Explicit-only Aether archive backfill and company enrichment entrypoint."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
SCOUT_DIR = REPO_ROOT / "scout"
for path in (str(SCRIPT_DIR), str(SCOUT_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from bulk_lib import BulkOptions, BulkRunner  # noqa: E402


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--since", required=True, help="inclusive YYYY-MM-DD")
    value.add_argument("--until", required=True, help="inclusive YYYY-MM-DD")
    value.add_argument(
        "--archive-until",
        default="",
        help="optional archive cutoff when later dates are supplied by a seed run",
    )
    value.add_argument("--output", type=Path, required=True)
    value.add_argument("--sources", type=Path, default=REPO_ROOT / "news_websites.csv")
    value.add_argument("--workers", type=int, default=5)
    value.add_argument("--model", default="grok-4.3")
    value.add_argument("--run-id", default="")
    value.add_argument("--resume", action="store_true")
    value.add_argument(
        "--refresh-why-lines",
        action="store_true",
        help=(
            "create a versioned recipient-facing A/B/C why-line revision using "
            "one Grok call per existing deduplicated company"
        ),
    )
    value.add_argument(
        "--why-limit",
        type=int,
        help="optional pilot size for --refresh-why-lines; resume without it to finish",
    )
    value.add_argument(
        "--reuse-discovery-corpus",
        action="store_true",
        help="resume from already persisted discovery pages without crawling sites again",
    )
    value.add_argument("--batch-size", type=int, default=20)
    value.add_argument("--seed-db", type=Path)
    value.add_argument("--seed-run-id", default="")
    value.add_argument(
        "--no-search-fallback",
        action="store_true",
        help="disable Grok search fallback for uncovered/incomplete sources",
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.resume and not args.run_id:
        print("ERROR: --resume requires --run-id", file=sys.stderr)
        return 2
    if args.refresh_why_lines and not args.resume:
        print("ERROR: --refresh-why-lines requires --resume", file=sys.stderr)
        return 2
    if args.why_limit is not None and not args.refresh_why_lines:
        print("ERROR: --why-limit requires --refresh-why-lines", file=sys.stderr)
        return 2
    if args.why_limit is not None and args.why_limit < 1:
        print("ERROR: --why-limit must be positive", file=sys.stderr)
        return 2
    if bool(args.seed_db) != bool(args.seed_run_id):
        print("ERROR: --seed-db and --seed-run-id must be supplied together", file=sys.stderr)
        return 2
    options = BulkOptions(
        since=args.since,
        until=args.until,
        archive_until=args.archive_until,
        output_dir=args.output.resolve(),
        sources_csv=args.sources.resolve(),
        workers=max(1, args.workers),
        model=args.model,
        run_id=args.run_id or str(uuid.uuid4()),
        resume=args.resume,
        seed_db=args.seed_db.resolve() if args.seed_db else None,
        seed_run_id=args.seed_run_id,
        search_fallback=not args.no_search_fallback,
        reuse_discovery_corpus=args.reuse_discovery_corpus,
        batch_size=max(1, min(args.batch_size, 25)),
    )
    try:
        runner = BulkRunner(options)
        result = (
            runner.refresh_why_lines(limit=args.why_limit)
            if args.refresh_why_lines
            else runner.run()
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
