"""Discover new article URLs from news_websites.csv; dedup against seen_urls.

Reuses: feedparser (RSS/Atom + dates + encodings), util.make_http_client +
        extract.resolve_article_url + canonicalize_url + sha256_hex + log_event,
        db.record_seen as the dedup gate.
Extend: discover_new_urls dispatch — add a source.method branch to support a new fetch type.
Failure: per-source try/except; broken feeds log `source_failed` and skip.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

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


MAX_LINKS_PER_SOURCE = 25

_ARTICLE_HINTS = (
    "article", "news", "business", "real-estate", "commercial",
    "development", "construction", "project", "opening", "lease", "tenant",
    "acquisition", "property", "multifamily", "industrial", "retail", "office",
    "azre", "blog",
)
_REJECT_SEGMENTS = {
    "about", "advertise", "author", "authors", "careers", "category", "contact",
    "events", "login", "privacy", "search", "subscribe", "tag", "terms",
}
_REJECT_EXTENSIONS = (
    ".css", ".csv", ".doc", ".docx", ".gif", ".ico", ".jpeg", ".jpg", ".js",
    ".json", ".mp3", ".mp4", ".pdf", ".png", ".rss", ".svg", ".webp", ".xml",
    ".zip",
)


def discover_new_urls(conn: sqlite3.Connection) -> list[NewArticle]:
    """Fetch every enabled source, dedup against seen_urls, return new articles."""
    out: list[NewArticle] = []
    with util.make_http_client() as client:
        for src in config.load_sources():
            db.sync_source(conn, src["name"], src["method"], src["endpoint"], src.get("enabled", False))
            if not src.get("enabled"):
                continue
            try:
                if src["method"] == "rss":
                    out.extend(_fetch_feed(client, src["name"], src["endpoint"], conn))
                elif src["method"] == "website":
                    out.extend(_scrape_website(client, src["name"], src["endpoint"], conn))
                else:
                    util.log_event(
                        "source_skipped",
                        source=src["name"],
                        reason=f"unknown method {src['method']!r}",
                    )
            except Exception as e:  # per-source isolation
                util.log_event("source_failed", source=src["name"], error=repr(e))
    return out


def _fetch_feed(
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


def _scrape_website(
    client: httpx.Client, source: str, url: str, conn: sqlite3.Connection,
) -> list[NewArticle]:
    resp = client.get(url)
    resp.raise_for_status()
    links = _candidate_article_links(resp.text, url)[:MAX_LINKS_PER_SOURCE]

    fresh: list[NewArticle] = []
    for link, title in links:
        try:
            resolved = extract.resolve_article_url(link)
            canon = util.canonicalize_url(resolved)
        except extract.ExtractError as e:
            util.log_event("article_url_resolve_failed", source=source, url=link, error=str(e))
            continue
        except ValueError:
            continue
        h = util.sha256_hex(canon)
        if db.record_seen(conn, h, canon, source, title):
            fresh.append(NewArticle(canon, h, source, title, _date_from_url(canon)))
    util.log_event("source_fetched", source=source, total=len(links), new=len(fresh))
    return fresh


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        attr_map = {k.lower(): v for k, v in attrs if k and v}
        href = attr_map.get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            self.links.append((self._href, text))
            self._href = None
            self._text = []


def _candidate_article_links(html: str, base_url: str) -> list[tuple[str, str]]:
    parser = _AnchorParser()
    parser.feed(html)

    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for href, text in parser.links:
        absolute = urljoin(base_url, href)
        parsed = urlsplit(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if not _same_site(parsed.hostname, urlsplit(base_url).hostname):
            continue
        try:
            canon = util.canonicalize_url(absolute)
        except ValueError:
            continue
        if canon in seen:
            continue
        if _article_score(canon, text, base_url) < 3:
            continue
        seen.add(canon)
        candidates.append((canon, text))
    return candidates


def _same_site(candidate_host: str | None, source_host: str | None) -> bool:
    if not candidate_host or not source_host:
        return False
    candidate = candidate_host.lower().removeprefix("www.")
    source = source_host.lower().removeprefix("www.")
    return candidate == source or candidate.endswith(f".{source}") or source.endswith(f".{candidate}")


def _article_score(url: str, title: str, base_url: str) -> int:
    parsed = urlsplit(url)
    base_path = urlsplit(base_url).path.rstrip("/") or "/"
    path = parsed.path.rstrip("/") or "/"
    if path == "/" or path == base_path:
        return 0
    lower_path = path.lower()
    if lower_path.endswith(_REJECT_EXTENSIONS):
        return 0
    segments = [s for s in lower_path.split("/") if s]
    if any(s in _REJECT_SEGMENTS for s in segments):
        return 0

    score = 0
    if _date_from_url(url):
        score += 3
    if any(hint in lower_path for hint in _ARTICLE_HINTS):
        score += 2
    slug = segments[-1] if segments else ""
    if len([part for part in slug.replace("-", " ").replace("_", " ").split() if part]) >= 4:
        score += 2
    if len(title.strip()) >= 25:
        score += 1
    return score


def _entry_date(entry) -> date | None:
    """feedparser exposes time.struct_time on published_parsed/updated_parsed."""
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    return date(t.tm_year, t.tm_mon, t.tm_mday) if t else None


def _date_from_url(url: str) -> date | None:
    parts = [p for p in urlsplit(url).path.split("/") if p]
    for i in range(len(parts) - 2):
        if not (parts[i].isdigit() and parts[i + 1].isdigit() and parts[i + 2].isdigit()):
            continue
        year = int(parts[i])
        if 2000 <= year <= 2100:
            try:
                return date(year, int(parts[i + 1]), int(parts[i + 2]))
            except ValueError:
                return None
    return None
