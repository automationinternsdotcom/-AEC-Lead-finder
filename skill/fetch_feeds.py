#!/usr/bin/env python3
"""Fetch Arizona CRE news from Google News RSS feeds.

Stdlib-only, no dependencies. Fetches all feeds in parallel,
deduplicates by URL, and outputs clean JSON to stdout.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

FEEDS = [
    ("az-cre", "Arizona commercial real estate when:30d"),
    ("phoenix-dev", "Phoenix office industrial retail development when:30d"),
    ("tucson-cre", "Tucson commercial real estate development when:30d"),
]

TIMEOUT_SECONDS = 15
USER_AGENT = "Mozilla/5.0 (compatible; AetherLeadFinder/1.0)"


def build_url(query: str) -> str:
    return (
        f"https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )


def strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    import re
    return unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def fetch_one(name: str, query: str) -> dict:
    """Fetch and parse a single RSS feed. Returns {name, items, error}."""
    url = build_url(query)
    items = []
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            desc = strip_html(item.findtext("description") or "")
            if title and link:
                items.append({
                    "source": name,
                    "title": title,
                    "link": link,
                    "published": pub,
                    "description": desc,
                })
        return {"name": name, "items": items, "error": None}
    except (URLError, ET.ParseError, OSError) as exc:
        return {"name": name, "items": [], "error": str(exc)}


def deduplicate(all_items: list[dict]) -> list[dict]:
    """Deduplicate by link URL, keeping the first occurrence."""
    seen: set[str] = set()
    unique = []
    for item in all_items:
        url = item["link"]
        if url not in seen:
            seen.add(url)
            unique.append(item)
    return unique


def main() -> int:
    errors = []
    all_items: list[dict] = []

    with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
        futures = {
            pool.submit(fetch_one, name, query): name
            for name, query in FEEDS
        }
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                errors.append(f"{result['name']}: {result['error']}")
            all_items.extend(result["items"])

    unique = deduplicate(all_items)

    output = {
        "total_fetched": len(all_items),
        "unique_items": len(unique),
        "errors": errors,
        "items": unique,
    }
    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
