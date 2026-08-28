"""Collection and judging stages of run.py."""
from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

# ``scout/run.py`` is launched as a file by the canonical pipeline. In that
# mode Python puts ``scout/`` ahead of the repository root on sys.path, which
# makes ``from pipeline`` resolve to ``scout/pipeline.py`` instead of the
# existing top-level ``pipeline`` package. Put the repository root first so the
# website fetcher imports consistently from both scripts and tests.
REPO_ROOT = Path(__file__).resolve().parent.parent
if sys.path[0] != str(REPO_ROOT):
    sys.path.insert(0, str(REPO_ROOT))

import article_judge
import config
import logbook
from pipeline import fetch as website_fetch
from pipeline import util as pipeline_util


def collect(window_days=1):
    """Scrape news_websites.csv in parallel -> unique entries by canonical link."""
    sources = _sources()

    def fetch_one(source):
        try:
            with pipeline_util.make_http_client() as client:
                resp = client.get(source["url"])
                resp.raise_for_status()
                links = website_fetch._candidate_article_links(resp.text, source["url"])
            return [
                {
                    "link": link,
                    "title": title,
                    "published_iso": (
                        website_fetch._date_from_url(link).isoformat()
                        if website_fetch._date_from_url(link)
                        else ""
                    ),
                    "source_site": _site(link),
                    "source_name": source["name"],
                }
                for link, title in links
            ]
        except Exception as e:
            logbook.log("collect", f"source failed {source['url']}: {e}")
            return []

    with ThreadPoolExecutor(max_workers=5) as pool:
        entry_lists = pool.map(fetch_one, sources)
        by_link = {
            entry["link"]: entry
            for entries in entry_lists
            for entry in entries
        }
    logbook.log("collect", f"{len(sources)} websites scraped, {len(by_link)} unique articles found")
    return list(by_link.values())


def filter_since(entries, since):
    """Keep entries with a URL publication date on or after ``since``.

    Curated website pages often expose large archives. Judging undated archive
    links would make a daily run spend hundreds of model calls and mix old news
    into today's digest, so undated entries are excluded from the daily path.
    """
    cutoff = date.fromisoformat(since)
    recent = []
    undated = 0
    stale = 0
    for entry in entries:
        published = entry.get("published_iso", "")
        if not published:
            undated += 1
            continue
        try:
            published_date = date.fromisoformat(published)
        except ValueError:
            undated += 1
            continue
        if published_date >= cutoff:
            recent.append(entry)
        else:
            stale += 1
    logbook.log(
        "freshness",
        f"{len(recent)} on/after {cutoff.isoformat()}, "
        f"{stale} older and {undated} undated skipped",
    )
    return recent


def judge(new_entries, workers):
    """Judge every entry -> (qualified, uncertain) rows, with progress logging."""
    total, progress, lock = len(new_entries), {"n": 0}, Lock()

    def judge_one(entry):
        try:
            row = article_judge.judge_article(entry)
        except Exception as e:
            logbook.log("judge", f"worker error: {e}")
            row = None
        with lock:
            progress["n"] += 1
            if progress["n"] % 25 == 0 or progress["n"] == total:
                logbook.log("judge", f"progress {progress['n']}/{total}")
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        judged_rows = list(pool.map(judge_one, new_entries))
    qualified, uncertain = [], []
    for row in judged_rows:
        if row is not None:
            split_by_confidence(row, qualified, uncertain)
    logbook.log(
        "judge",
        f"{len(qualified)} kept, {len(uncertain)} uncertain, "
        f"{len(judged_rows) - len(qualified) - len(uncertain)} rejected",
    )
    return qualified, uncertain


def split_by_confidence(row, qualified, uncertain):
    (uncertain if row.pop("confidence", "high") == "low" else qualified).append(row)


def _sources():
    with open(config.NEWS_WEBSITES_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [
        {"name": (r.get("Resource Name") or r.get("name") or "").strip(),
         "url": (r.get("URL") or r.get("url") or "").strip()}
        for r in rows
        if (r.get("URL") or r.get("url") or "").strip()
    ]


def _site(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"
