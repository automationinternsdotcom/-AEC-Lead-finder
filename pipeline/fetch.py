"""Discover new article URLs from sources.yaml; dedup against seen_urls.

Reuses: feedparser (RSS/Atom + dates + encodings), util.make_http_client +
        extract.resolve_article_url + canonicalize_url + sha256_hex + log_event,
        db.record_seen as the dedup gate.
Extend: METHOD_HANDLERS dispatch — add one key to support a new fetch type.
Failure: per-source try/except; broken feeds log `source_failed` and skip.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Callable

import feedparser
import httpx

from pipeline import config, db, extract, util


@dataclass(slots=True)
class NewArticle:
    url: str
    url_hash: str
    source: str
    title: str
    published_at: date | None


# Dispatch table: source.method → (endpoint → fetch_url). Extend here.
METHOD_HANDLERS: dict[str, Callable[[str], str]] = {
    "rss": lambda endpoint: endpoint,
    "google_news": util.build_google_news_url,
}


def discover_new_urls(conn: sqlite3.Connection) -> list[NewArticle]:
    """Fetch every enabled source, dedup against seen_urls, return new articles."""
    out: list[NewArticle] = []
    with util.make_http_client() as client:
        for src in config.load_sources():
            db.sync_source(conn, src["name"], src["method"], src["endpoint"], src.get("enabled", False))
            if not src.get("enabled"):
                continue
            handler = METHOD_HANDLERS.get(src["method"])
            if handler is None:
                util.log_event("source_skipped", source=src["name"], reason=f"unknown method {src['method']!r}")
                continue
            try:
                out.extend(_fetch_one(client, src["name"], handler(src["endpoint"]), conn))
            except Exception as e:  # per-source isolation
                util.log_event("source_failed", source=src["name"], error=repr(e))
    return out


def _fetch_one(
    client: httpx.Client, source: str, url: str, conn: sqlite3.Connection,
) -> list[NewArticle]:
    resp = client.get(url)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if not parsed.entries:
        if parsed.bozo:
            raise ValueError(f"feed parse error: {parsed.bozo_exception!r}")
        util.log_event("source_fetched", source=source, total=0, new=0)
        return []

    fresh: list[NewArticle] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", None)
        if not link:
            continue
        try:
            resolved = extract.resolve_article_url(link)
            canon = util.canonicalize_url(resolved)
        except extract.ExtractError as e:
            util.log_event("article_url_resolve_failed", source=source, url=link, error=str(e))
            continue
        except ValueError:
            continue
        h = util.sha256_hex(canon)
        title = getattr(entry, "title", None)
        if db.record_seen(conn, h, canon, source, title):
            fresh.append(NewArticle(canon, h, source, title or "", _entry_date(entry)))
    util.log_event("source_fetched", source=source, total=len(parsed.entries), new=len(fresh))
    return fresh


def _entry_date(entry) -> date | None:
    """feedparser exposes time.struct_time on published_parsed/updated_parsed."""
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    return date(t.tm_year, t.tm_mon, t.tm_mday) if t else None
