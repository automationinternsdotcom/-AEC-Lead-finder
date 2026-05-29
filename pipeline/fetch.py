from __future__ import annotations

from datetime import datetime, timezone

import feedparser
import httpx

from schema import FetchedPage, SourceRecord
from pipeline import util


MIN_CLEAN_TEXT_CHARS = 200


def discover_rss_pages(source: SourceRecord, http: httpx.Client) -> list[FetchedPage]:
    if not source.rss_url:
        return []

    response = http.get(source.rss_url)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)

    pages: list[FetchedPage] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", None)
        if not link:
            continue
        try:
            url = util.canonicalize_url(link)
        except ValueError:
            continue
        pages.append(
            FetchedPage(
                source_id=source.source_id,
                source_site=source.site_name,
                source_type=source.source_type,
                url=url,
                title=getattr(entry, "title", None),
                published_at=_entry_datetime(entry),
            )
        )
    return pages


def fetch_page_content(page: FetchedPage, http: httpx.Client) -> FetchedPage:
    response = http.get(page.url)
    response.raise_for_status()
    cleaned_text = util.clean_html(response.text, page.url)
    if len(cleaned_text) < MIN_CLEAN_TEXT_CHARS:
        raise ValueError("page_clean_text_too_short")
    return page.model_copy(update={"raw_html": response.text, "cleaned_text": cleaned_text})


def _entry_datetime(entry: object) -> datetime | None:
    parsed_time = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed_time:
        return None
    return datetime(
        parsed_time.tm_year,
        parsed_time.tm_mon,
        parsed_time.tm_mday,
        parsed_time.tm_hour,
        parsed_time.tm_min,
        parsed_time.tm_sec,
        tzinfo=timezone.utc,
    )

