# /// script
# requires-python = ">=3.12"
# dependencies = ["certifi", "dnspython", "feedparser", "httpx", "pydantic>=2.8", "python-dotenv"]
# ///
"""Canonical Aether AEC Scout V2 entrypoint.

The CLI remains the compatibility boundary used by local and GitHub automation.
All stages run in-process and persist resumable state before the next begins.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import config
from v2.orchestrator import PipelineOptions, PipelineRunner


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--workers", type=int, default=5)
    value.add_argument(
        "--discover-states",
        type=int,
        default=0,
        help="accepted for GPS CLI compatibility; curated AEC discovery remains Arizona-only",
    )
    value.add_argument("--apollo-go", action="store_true", help="authorize billable Apollo lookups")
    value.add_argument(
        "--apollo-phones",
        action="store_true",
        help="separately authorize Apollo phone reveal (requires --apollo-go and APOLLO_WEBHOOK_URL)",
    )
    value.add_argument(
        "--max-articles",
        type=int,
        default=0,
        help="qualify at most N new candidates; deferred candidates enter review (0 = no limit)",
    )
    value.add_argument("--since", default=(date.today() - timedelta(days=1)).isoformat())
    value.add_argument("--stamp", default=date.today().isoformat())
    value.add_argument("--run-id", default="", help="persistent UUID for resume/retry")
    value.add_argument("--resume", action="store_true", help="resume an existing --run-id")
    value.add_argument(
        "--retry-review",
        action="store_true",
        help="retry eligible quarantined records while resuming",
    )
    value.add_argument("--newsapi", action="store_true", help="manually enable the NewsAPI adapter")
    value.add_argument("--apify", action="store_true", help="manually enable the Apify/Facebook adapter")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.apollo_phones and not args.apollo_go:
        print("ERROR: --apollo-phones requires --apollo-go", file=sys.stderr)
        return 2
    options = PipelineOptions(
        db_path=config.DB_PATH,
        results_dir=config.RESULTS_DIR,
        sources_csv=config.NEWS_WEBSITES_CSV,
        stamp=args.stamp,
        since=args.since,
        workers=args.workers,
        max_articles=args.max_articles,
        run_id=args.run_id,
        resume=args.resume,
        retry_review=args.retry_review,
        apollo_go=args.apollo_go,
        apollo_phones=args.apollo_phones,
        phone_webhook=os.environ.get("APOLLO_WEBHOOK_URL", ""),
        newsapi=args.newsapi,
        apify=args.apify,
        grok_model=config.GROK_MODEL,
        extractor_model=config.EXTRACTOR_MODEL,
    )
    try:
        result = PipelineRunner(options).run()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"run_id={result.run_id}", file=sys.stderr)
    print(f"manifest={result.manifest_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
