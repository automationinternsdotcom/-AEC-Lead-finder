"""Expand discovered sources into fetch-ready article/page rows."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import feedparser

from pipeline import util
from pipeline.contracts import ArtifactEnvelope
from pipeline.url_classifier import UrlKind, classify_url


@dataclass(frozen=True)
class FetchRow:
    url_hash: str
    url: str
    source: str
    title: str = ""
    source_url: str | None = None
    source_type: str | None = None

    def to_dict(self) -> dict[str, str]:
        row = {
            "url_hash": self.url_hash,
            "url": self.url,
            "source": self.source,
            "title": self.title,
        }
        if self.source_url:
            row["source_url"] = self.source_url
        if self.source_type:
            row["source_type"] = self.source_type
        return row


@dataclass(frozen=True)
class ClassifiedSource:
    url: str
    canonical_url: str
    source_name: str
    source_type: UrlKind
    title: str = ""
    expanded_count: int = 0
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "canonical_url": self.canonical_url,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "title": self.title,
            "expanded_count": self.expanded_count,
            "skipped_reason": self.skipped_reason,
        }


def expand_discovery_artifact(
    artifact: ArtifactEnvelope,
    *,
    dedupe_namespace: str,
    max_entries_per_source: int = 25,
) -> tuple[list[FetchRow], list[ClassifiedSource]]:
    if artifact.stage != "discover":
        raise ValueError("source expansion requires a discover artifact")

    rows: list[FetchRow] = []
    classified: list[ClassifiedSource] = []
    seen_final_urls: set[str] = set()
    for record in artifact.records:
        source_url = record.get("canonical_url") or record["url"]
        canonical = util.canonicalize_url(source_url)
        source_name = record.get("source_name") or record.get("discovered_via") or "gemini"
        title = record.get("title") or ""
        source_type = classify_url(canonical, record.get("source_type"))
        expanded = _expand_record(
            canonical,
            source_type,
            source_name=source_name,
            source_title=title,
            dedupe_namespace=dedupe_namespace,
            max_entries=max_entries_per_source,
        )
        fresh_rows: list[FetchRow] = []
        for row in expanded:
            final_canonical = util.canonicalize_url(row.url)
            if final_canonical in seen_final_urls:
                continue
            seen_final_urls.add(final_canonical)
            fresh_rows.append(row)
        rows.extend(fresh_rows)
        classified.append(ClassifiedSource(
            url=record["url"],
            canonical_url=canonical,
            source_name=source_name,
            source_type=source_type,
            title=title,
            expanded_count=len(fresh_rows),
            skipped_reason=None if fresh_rows else _skip_reason(source_type),
        ))
    return rows, classified


def _expand_record(
    url: str,
    source_type: UrlKind,
    *,
    source_name: str,
    source_title: str,
    dedupe_namespace: str,
    max_entries: int,
) -> list[FetchRow]:
    if source_type in {"article", "permit_listing", "market_report", "company_page", "public_database", "directory"}:
        return [_row(url, source_name, source_title, url, source_type, dedupe_namespace)]
    if source_type in {"rss_feed", "atom_feed"}:
        return _expand_feed(url, source_name, source_type, dedupe_namespace, max_entries)
    if source_type == "sitemap":
        return _expand_sitemap(url, source_name, source_type, dedupe_namespace, max_entries)
    return []


def _expand_feed(
    url: str,
    source_name: str,
    source_type: str,
    dedupe_namespace: str,
    max_entries: int,
) -> list[FetchRow]:
    parsed = feedparser.parse(url)
    rows: list[FetchRow] = []
    for entry in parsed.entries[:max_entries]:
        link = getattr(entry, "link", None) or entry.get("link")
        if not link:
            continue
        title = getattr(entry, "title", None) or entry.get("title") or ""
        rows.append(_row(link, source_name, title, url, source_type, dedupe_namespace))
    return rows


def _expand_sitemap(
    url: str,
    source_name: str,
    source_type: str,
    dedupe_namespace: str,
    max_entries: int,
) -> list[FetchRow]:
    with util.make_http_client() as client:
        response = client.get(url)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    urls: list[str] = []
    for loc in root.iter():
        if loc.tag.endswith("loc") and loc.text and loc.text.strip().startswith(("http://", "https://")):
            urls.append(loc.text.strip())
    return [
        _row(item, source_name, "", url, source_type, dedupe_namespace)
        for item in urls[:max_entries]
    ]


def _row(
    url: str,
    source_name: str,
    title: str,
    source_url: str,
    source_type: str,
    dedupe_namespace: str,
) -> FetchRow:
    canonical = util.canonicalize_url(url)
    return FetchRow(
        url_hash=util.sha256_hex(f"{dedupe_namespace}|{canonical}"),
        url=canonical,
        source=source_name,
        title=title,
        source_url=source_url,
        source_type=source_type,
    )


def _skip_reason(source_type: UrlKind) -> str | None:
    if source_type in {"homepage", "search_result", "unsupported", "other"}:
        return f"unsupported_source_type:{source_type}"
    return None
