"""Discover new article URLs from a CampaignSpec-selected source set.

Reuses: feedparser (RSS/Atom + dates + encodings), util.make_http_client +
        canonicalize_url + sha256_hex + log_event, db.record_seen as the dedup gate.
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

from pipeline import config, db, util
from pipeline.spec import CampaignSpec


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


def sources_for_campaign(
    spec: CampaignSpec | None,
    source_registry: list[dict] | None = None,
) -> list[dict]:
    """Return the concrete source rows this run should fetch.

    Without a spec, this preserves the legacy behavior: fetch enabled rows from
    sources.yaml. With a spec, sources.yaml acts as a registry and
    spec.discovery.source_tags/search_queries choose the active rows.
    """
    sources = source_registry if source_registry is not None else config.load_sources()
    if spec is None:
        return [dict(src) for src in sources if src.get("enabled")]

    selected: list[dict] = []
    seen_names: set[str] = set()
    tags = set(spec.discovery.source_tags)

    for src in sources:
        if src.get("name") not in tags:
            continue
        row = dict(src)
        endpoint = str(row.get("endpoint") or "").strip()
        if not endpoint or endpoint == "TODO":
            raise ValueError(
                f"campaign {spec.campaign_id!r} selected source "
                f"{row.get('name')!r} without a real endpoint"
            )
        row["enabled"] = True
        selected.append(row)
        seen_names.add(row["name"])

    selected_google_queries = {
        row["endpoint"]
        for row in selected
        if row.get("method") == "google_news"
    }
    for idx, query in enumerate(spec.discovery.search_queries, start=1):
        if query in selected_google_queries:
            continue
        name = f"{spec.campaign_id}_google_news_{idx:02d}"
        if name in seen_names:
            continue
        selected.append({
            "name": name,
            "method": "google_news",
            "endpoint": query,
            "enabled": True,
        })
        seen_names.add(name)

    return selected


def discover_new_urls(
    conn: sqlite3.Connection,
    spec: CampaignSpec | None = None,
) -> list[NewArticle]:
    """Fetch selected sources, dedup against seen_urls, return new articles."""
    out: list[NewArticle] = []
    with util.make_http_client() as client:
        for src in sources_for_campaign(spec):
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
            canon = util.canonicalize_url(link)
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
