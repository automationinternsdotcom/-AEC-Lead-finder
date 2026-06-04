"""Two-month historical article discovery for Friday-demo backfills.

Daily sources are intentionally tight. Backfill sources are broader and run on
demand so Jordan can see progress against "past ~2 months of news" without
permanently increasing daily noise.
"""
from __future__ import annotations

import re
import sqlite3

from pipeline import fetch


BACKFILL_QUERY_BASES = (
    "Arizona commercial real estate lease",
    "Phoenix commercial real estate development",
    "Tucson commercial real estate development",
    "Arizona multifamily lease-up apartment development",
    "Phoenix office lease tenant move in",
    "Arizona industrial warehouse lease expansion",
    "Arizona retail opening restaurant coffee shop",
    "Arizona property management change commercial real estate",
    "Phoenix redevelopment renovation adaptive reuse",
    "Arizona medical office development lease",
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def build_backfill_sources(days: int = 60) -> list[dict]:
    if days <= 0:
        raise ValueError("days must be positive")
    sources = []
    for q in BACKFILL_QUERY_BASES:
        slug = _SLUG_RE.sub("_", q.lower()).strip("_")[:60]
        sources.append({
            "name": f"backfill_{days}d_{slug}",
            "method": "google_news",
            "endpoint": f"{q} when:{days}d",
            "enabled": True,
        })
    return sources


def discover_backfill_urls(conn: sqlite3.Connection, days: int = 60) -> list[fetch.NewArticle]:
    return fetch.discover_urls_from_sources(conn, build_backfill_sources(days))
