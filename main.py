"""Pipeline orchestrator (root entrypoint).

Run with: `python main.py` (local) or `uv run python main.py` (GHA).
Exit code: 0 = run ok, 1 = setup/finalize failure (per-article failures don't fail the run).

Reuses: every pipeline.* module, anthropic.Anthropic (one client per run),
        collections.Counter for bookkeeping, util.log_event + make_http_client.
Extend: _process_article is the per-article try/except boundary — add new steps inside it.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from collections import Counter

from anthropic import Anthropic

from pipeline import config, db, enrich, extract, fetch, push, util
from pipeline.fetch import NewArticle


def run() -> int:
    """Execute one daily pipeline run. Returns process exit code."""
    settings = config.settings()
    started_at = util.utc_now_iso()
    t0 = time.monotonic()
    counters: Counter = Counter()

    conn = db.connect()
    db.insert_run(conn, started_at)
    conn.commit()

    try:
        rates = config.load_rates()
        new_urls = fetch.discover_new_urls(conn)[: settings.max_articles_per_run]
        conn.commit()
        counters["articles_new"] = len(new_urls)

        llm = Anthropic(api_key=settings.anthropic_api_key)
        with util.make_http_client() as http:
            for na in new_urls:
                _process_article(conn, na, settings, rates, http, llm, counters)
                conn.commit()

        duration = int(time.monotonic() - t0)
        db.finalize_run(conn, started_at, dict(counters), "ok", duration)
        conn.commit()
        util.log_event("run_finished", status="ok", duration_sec=duration, **counters)
        return 0

    except Exception as e:
        duration = int(time.monotonic() - t0)
        db.finalize_run(conn, started_at, dict(counters), "failed", duration, repr(e))
        conn.commit()
        util.log_event("run_failed", status="failed", duration_sec=duration, error=repr(e), **counters)
        return 1
    finally:
        conn.close()


def _process_article(
    conn: sqlite3.Connection, na: NewArticle, settings: config.Settings,
    rates: dict, http, llm: Anthropic, counters: Counter,
) -> None:
    """One article's work, fully wrapped in try/except so bad articles don't fail the run."""
    try:
        article = extract.extract_article(na.url, http, llm)
        db.mark_seen_status(conn, na.url_hash, "extracted")

        passes, reason = extract.is_qualifying(article)
        if not passes:
            db.mark_seen_status(conn, na.url_hash, "filtered")
            util.log_event("article_dropped", url=na.url, reason=reason)
            return
        counters["articles_az"] += 1

        est_value, basis = extract.estimate_deal_size(article, rates)
        lead = enrich.find_lead(article.company_domain_guess, settings)
        org_id, person_id, deal_id = push.sync_to_pipedrive(
            article, lead, est_value, basis, na.url, settings,
        )
        db.upsert_article(conn, na.url_hash, article.model_dump_json(), est_value, lead is None)
        db.attach_pipedrive_ids(conn, na.url_hash, org_id, person_id, deal_id)
        db.mark_seen_status(conn, na.url_hash, "pushed")
        counters["articles_pushed"] += 1
        util.log_event("article_pushed", url=na.url, deal_id=deal_id, lead_gap=lead is None)

    except Exception as e:
        db.mark_seen_status(conn, na.url_hash, "failed")
        util.log_event("article_failed", url=na.url, error=repr(e))


if __name__ == "__main__":
    sys.exit(run())
