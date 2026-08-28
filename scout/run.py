# /// script
# requires-python = ">=3.12"
# dependencies = ["feedparser", "googlenewsdecoder", "httpx", "pydantic>=2.8", "python-dotenv", "pyyaml", "trafilatura"]
# ///
"""news_websites.csv scrape -> Grok judges each article -> DB -> dedup -> dated CSV."""
from __future__ import annotations

import argparse
import csv
import os
from datetime import date, timedelta

import config
import csvio
import db
import extractor
import logbook
import stages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--discover-states", type=int, default=0, help="accepted for GPS CLI compatibility; ignored")
    parser.add_argument("--since", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--stamp", default=date.today().isoformat())
    parser.add_argument("--max-articles", type=int, default=0)
    args = parser.parse_args()

    db.init_db()
    unique_entries = stages.collect()
    unique_entries = stages.filter_since(unique_entries, args.since)
    known_links = db.all_links()
    new_entries = [entry for entry in unique_entries if entry["link"] not in known_links]
    logbook.log("judge", f"{len(unique_entries) - len(new_entries)} already in DB, skipped")
    if args.max_articles > 0 and len(new_entries) > args.max_articles:
        dropped = len(new_entries) - args.max_articles
        new_entries = new_entries[: args.max_articles]
        logbook.log("judge", f"spend cap: dropped {dropped} articles over --max-articles {args.max_articles}")

    qualified_rows, uncertain_rows = stages.judge(new_entries, args.workers)
    for row in qualified_rows + uncertain_rows:
        db.insert_article(row)

    export_dir = os.path.join(config.RESULTS_DIR, args.stamp)
    os.makedirs(export_dir, exist_ok=True)

    def write_csv(filename, rows, dedup=False):
        path = os.path.join(export_dir, filename)
        fields = list(db.ARTICLE_COLUMNS)
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                old_rows = list(csv.DictReader(f))
            fields += [c for c in (old_rows[0] if old_rows else {}) if c not in fields]
            new_links = {row["link"] for row in rows}
            rows = rows + [row for row in old_rows if row["link"] not in new_links]
        if dedup:
            merged_in = len(rows)
            rows = extractor.dedup_leads(rows)
            for row in rows:
                row.setdefault("aka", "")
            logbook.log("dedup", f"{merged_in} in, {len(rows)} merged out")
        csvio.write_csv(path, rows, fields)
        logbook.log("export", f"wrote {len(rows)} rows to {path}")
        print(f"wrote {len(rows)} rows to {path}")

    write_csv("raw_leads.csv", qualified_rows, dedup=True)
    if uncertain_rows:
        write_csv("uncertain_leads.csv", uncertain_rows)


if __name__ == "__main__":
    main()
