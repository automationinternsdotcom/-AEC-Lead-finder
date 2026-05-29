from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from schema import FetchedPage, SourceRecord
from pipeline import util


LIKELY_POST_PATHS = (
    "/news",
    "/blog",
    "/press",
    "/projects",
    "/development",
    "/properties",
    "/insights",
)


def discover_website_pages(source: SourceRecord, http: httpx.Client, max_pages: int) -> list[FetchedPage]:
    start_url = source.crawl_start_url or source.source_url
    response = http.get(start_url)
    response.raise_for_status()

    links = util.extract_links(response.text, start_url)
    allowed_links = [
        link
        for link in links
        if util.same_domain(link, source.source_url) and util.allowed_by_path(link, source.allowed_paths)
    ]
    ranked_links = sorted(allowed_links, key=_candidate_rank, reverse=True)

    pages: list[FetchedPage] = []
    for url in ranked_links[:max_pages]:
        pages.append(
            FetchedPage(
                source_id=source.source_id,
                source_site=source.site_name,
                source_type=source.source_type,
                url=url,
                title=_title_from_url(url),
            )
        )
    return pages


def _candidate_rank(url: str) -> int:
    path = urlsplit(url).path.lower()
    score = 0
    if any(path.startswith(prefix) for prefix in LIKELY_POST_PATHS):
        score += 10
    if any(word in path for word in ("opening", "lease", "development", "construction", "project")):
        score += 5
    if path.count("/") >= 2:
        score += 2
    return score


def _title_from_url(url: str) -> str:
    slug = (urlsplit(url).path.rstrip("/").split("/")[-1] or "").replace("-", " ").strip()
    return slug.title() if slug else urlsplit(url).hostname or url

