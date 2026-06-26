"""`python -m pipeline.cli.email_digest` — email a summary of recent Leads.

Reads Leads back out of Pipedrive, renders a one-row-per-Lead summary table
(every contact listed) and sends it over SMTP to LEAD_DIGEST_TO.

Modes (exactly one required):
  --daily             Leads created since the last successful digest run (a
                      watermark in SQLite; first run falls back to today UTC).
                      Appended to the daily routine. A successful send advances
                      the watermark; a failed send leaves it so the next run
                      retries. --print does NOT advance it.
  --since YYYY-MM-DD   Leads created on/after that date (one-time backfill). Does
                      NOT touch the daily watermark.

Other flags:
  --print   Render to stdout instead of sending — preview without SMTP creds.

Exit codes:
  0  ok (sent, previewed, or nothing to send)
  2  usage error (bad/missing mode)
  4  SMTP not configured (set SMTP_HOST / LEAD_DIGEST_TO / LEAD_DIGEST_FROM)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone

from pipeline import config, db, email_digest, util


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pipeline.cli.email_digest")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--daily", action="store_true",
                   help="Leads created today (UTC).")
    g.add_argument("--since", metavar="YYYY-MM-DD",
                   help="Leads created on/after this date (backfill).")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="Render to stdout instead of sending.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # run_at is the watermark we'll persist if a --daily send succeeds. Capture
    # it BEFORE querying so Leads created during the run aren't skipped next time.
    run_at = util.utc_now_iso()

    if args.daily:
        since, window_label = _daily_window()
    else:
        try:
            d = date.fromisoformat(args.since)
        except ValueError:
            sys.stderr.write(f"Invalid --since date: {args.since!r} (want YYYY-MM-DD)\n")
            return 2
        since = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        window_label = f"since {d.isoformat()}"

    settings = config.settings()
    with email_digest.make_pipedrive_client(settings) as http:
        leads = email_digest.list_leads_since(http, settings, since)

    util.log_event("digest_collected", window=window_label, count=len(leads))

    if not leads:
        # Advance the watermark on an empty daily run too — there's nothing to
        # send and nothing newer would be missed (run_at predates the query).
        if args.daily and not args.print_only:
            _record_daily_run(run_at, 0)
        util.log_event("digest_skipped", reason="no_new_leads", window=window_label)
        return 0

    if args.print_only:
        sys.stdout.write(email_digest.render_html(leads, window_label))
        sys.stdout.write("\n")
        return 0

    msg = email_digest.build_message(leads, window_label, settings)
    try:
        email_digest.send_digest(msg, settings)
    except email_digest.SmtpNotConfigured as e:
        sys.stderr.write(f"{e}\n")
        return 4

    # Only after a successful send do we advance the daily watermark.
    if args.daily:
        _record_daily_run(run_at, len(leads))

    util.log_event("digest_sent", window=window_label, count=len(leads),
                   to=settings.lead_digest_to)
    return 0


def _daily_window() -> tuple[datetime, str]:
    """(since, label) for --daily: the last successful digest run, or today UTC."""
    conn = db.connect()
    try:
        last = db.get_last_digest_run(conn)
    finally:
        conn.close()
    if last:
        return email_digest.parse_watermark(last), f"since last run ({last})"
    since = email_digest.start_of_today_utc()
    return since, f"today ({since.date().isoformat()})"


def _record_daily_run(run_at: str, lead_count: int) -> None:
    conn = db.connect()
    try:
        db.record_digest_run(conn, run_at, lead_count)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
