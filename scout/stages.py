"""Collection and judging stages of run.py."""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.parse import urlparse

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
                    "published_iso": "",
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
