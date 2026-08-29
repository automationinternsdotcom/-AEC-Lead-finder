"""Curated-site and learned-feed discovery behind one typed contract."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import feedparser
from pydantic import BaseModel, ConfigDict, Field

from .artifacts import ArtifactStore
from .contracts import DiscoveryCandidate, FeedStatus, RecordStatus, ReviewItem
from .http import FetchResponse, HttpFetcher
from .ids import candidate_id, canonicalize_url, stable_hash, stable_uuid
from .state import StateStore


MAX_LINKS_PER_SOURCE = 25
COMMON_FEED_PATHS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml")
ARTICLE_HINTS = (
    "article",
    "news",
    "business",
    "real-estate",
    "commercial",
    "development",
    "construction",
    "project",
    "opening",
    "lease",
    "tenant",
    "acquisition",
    "property",
    "multifamily",
    "industrial",
    "retail",
    "office",
    "azre",
    "blog",
)
REJECT_SEGMENTS = {
    "about",
    "advertise",
    "author",
    "authors",
    "careers",
    "category",
    "contact",
    "events",
    "login",
    "privacy",
    "search",
    "subscribe",
    "tag",
    "terms",
}
REJECT_EXTENSIONS = (
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".rss",
    ".svg",
    ".webp",
    ".xml",
    ".zip",
)
DATE_META_KEYS = {
    "article:published_time",
    "date",
    "datepublished",
    "dc.date",
    "parsely-pub-date",
    "pubdate",
    "publishdate",
    "sailthru.date",
}
MULTIPART_SUFFIXES = {"co.uk", "com.au", "com.br", "co.nz", "co.jp", "com.mx"}


class CuratedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    name: str
    url: str
    domain: str
    state: str = "Arizona"
    enabled: bool = True


class DiscoveryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[DiscoveryCandidate] = Field(default_factory=list)
    reviews: list[ReviewItem] = Field(default_factory=list)
    source_errors: list[dict] = Field(default_factory=list)


@dataclass(slots=True)
class ParsedIndex:
    article_links: list[tuple[str, str]]
    feed_links: list[str]


class _IndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.feed_links: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {str(key).lower(): value for key, value in attrs if key and value}
        if tag.lower() == "a" and self._href is None and attr_map.get("href"):
            self._href = str(attr_map["href"])
            self._text = []
        if tag.lower() == "link":
            rel = {part.casefold() for part in str(attr_map.get("rel", "")).split()}
            media_type = str(attr_map.get("type", "")).casefold()
            href = attr_map.get("href")
            if "alternate" in rel and href and media_type in {
                "application/rss+xml",
                "application/atom+xml",
                "application/feed+json",
            }:
                self.feed_links.append(str(href))

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None
            self._text = []


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.date_values: list[str] = []
        self.json_ld: list[str] = []
        self._json_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {str(key).lower(): value for key, value in attrs if key and value}
        if tag.lower() == "meta":
            key = str(attr_map.get("property") or attr_map.get("name") or attr_map.get("itemprop") or "").casefold()
            content = attr_map.get("content")
            if key in DATE_META_KEYS and content:
                self.date_values.append(str(content))
        elif tag.lower() == "time" and attr_map.get("datetime"):
            self.date_values.append(str(attr_map["datetime"]))
        elif tag.lower() == "script" and str(attr_map.get("type", "")).casefold() == "application/ld+json":
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._json_parts is not None:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._json_parts is not None:
            self.json_ld.append("".join(self._json_parts))
            self._json_parts = None


def load_curated_sources(path: str | Path) -> list[CuratedSource]:
    with Path(path).open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    sources: list[CuratedSource] = []
    for row in rows:
        url = str(row.get("URL") or row.get("url") or "").strip()
        if not url:
            continue
        if "://" not in url:
            url = f"https://{url}"
        canonical = canonicalize_url(url)
        name = str(
            row.get("Resource Name")
            or row.get("name")
            or urlsplit(canonical).hostname
            or ""
        ).strip()
        domain = (urlsplit(canonical).hostname or "").lower()
        sources.append(
            CuratedSource(
                source_id=stable_uuid("source", canonical),
                name=name,
                url=canonical,
                domain=domain,
            )
        )
    return sources


def parse_index(html: str, base_url: str) -> ParsedIndex:
    parser = _IndexParser()
    parser.feed(html)
    seen: set[str] = set()
    articles: list[tuple[str, str]] = []
    for href, title in parser.links:
        try:
            url = canonicalize_url(urljoin(base_url, href))
        except ValueError:
            continue
        if not same_site(url, base_url) or url in seen or article_score(url, title, base_url) < 3:
            continue
        seen.add(url)
        articles.append((url, title))
    feeds: list[str] = []
    for href in parser.feed_links:
        try:
            url = canonicalize_url(urljoin(base_url, href))
        except ValueError:
            continue
        if same_registrable_domain(url, base_url) and url not in feeds:
            feeds.append(url)
    return ParsedIndex(articles[:MAX_LINKS_PER_SOURCE], feeds)


def article_score(url: str, title: str, base_url: str) -> int:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    base_path = urlsplit(base_url).path.rstrip("/") or "/"
    if path in {"/", base_path} or path.casefold().endswith(REJECT_EXTENSIONS):
        return 0
    segments = [segment for segment in path.casefold().split("/") if segment]
    if any(segment in REJECT_SEGMENTS for segment in segments):
        return 0
    score = 3 if date_from_url(url) else 0
    if any(hint in path.casefold() for hint in ARTICLE_HINTS):
        score += 2
    slug_words = re.split(r"[-_\s]+", segments[-1] if segments else "")
    if len([word for word in slug_words if word]) >= 4:
        score += 2
    if len(title.strip()) >= 25:
        score += 1
    return score


def publication_date(html: str, url: str) -> datetime | None:
    """Prefer structured page metadata and only then inspect the URL."""
    parser = _MetadataParser()
    parser.feed(html)
    values = list(parser.date_values)
    for raw in parser.json_ld:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        values.extend(_json_ld_dates(payload))
    for value in values:
        parsed = parse_datetime(value)
        if parsed:
            return parsed
    from_url = date_from_url(url)
    return datetime.combine(from_url, time.min, tzinfo=timezone.utc) if from_url else None


def parse_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def date_from_url(url: str) -> date | None:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    for index in range(len(parts) - 2):
        if not all(part.isdigit() for part in parts[index : index + 3]):
            continue
        year = int(parts[index])
        if 2000 <= year <= 2100:
            try:
                return date(year, int(parts[index + 1]), int(parts[index + 2]))
            except ValueError:
                return None
    match = re.search(r"(?<!\d)(20\d{2})[-_](\d{1,2})[-_](\d{1,2})(?!\d)", urlsplit(url).path)
    if match:
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            return None
    return None


def same_site(left: str, right: str) -> bool:
    a = (urlsplit(left).hostname or "").casefold().removeprefix("www.")
    b = (urlsplit(right).hostname or "").casefold().removeprefix("www.")
    return bool(a and b and (a == b or a.endswith(f".{b}") or b.endswith(f".{a}")))


def registrable_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold().strip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in MULTIPART_SUFFIXES and len(labels) >= 3 else suffix


def same_registrable_domain(left: str, right: str) -> bool:
    return bool(registrable_domain(left) and registrable_domain(left) == registrable_domain(right))


class CuratedSiteAdapter:
    name = "curated"

    def __init__(
        self,
        sources: Iterable[CuratedSource],
        state: StateStore,
        artifacts: ArtifactStore,
        fetch: Callable[[str], FetchResponse] | None = None,
        workers: int = 5,
    ):
        self.sources = [source for source in sources if source.enabled]
        self.state = state
        self.artifacts = artifacts
        self.fetch = fetch or HttpFetcher()
        self.workers = workers

    def discover(
        self,
        run_id: str,
        since: date,
        max_candidates: int = 0,
        until: date | None = None,
    ) -> DiscoveryBatch:
        for source in self.sources:
            self.state.upsert_source(
                source.source_id, source.name, source.url, source.domain, source.state, source.enabled
            )
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            source_batches = list(
                pool.map(
                    lambda source: self._discover_source(run_id, since, until, source),
                    self.sources,
                )
            )
        batch = DiscoveryBatch()
        by_id: dict[str, DiscoveryCandidate] = {}
        for source_batch in source_batches:
            batch.reviews.extend(source_batch.reviews)
            batch.source_errors.extend(source_batch.source_errors)
            for candidate in source_batch.candidates:
                by_id.setdefault(candidate.candidate_id, candidate)
        candidates = list(by_id.values())
        if max_candidates > 0:
            candidates = candidates[:max_candidates]
        for candidate in candidates:
            self.state.save_candidate(candidate)
        for review in batch.reviews:
            self.state.add_review(review)
        batch.candidates = candidates
        return batch

    def _discover_source(
        self,
        run_id: str,
        since: date,
        until: date | None,
        source: CuratedSource,
    ) -> DiscoveryBatch:
        batch = DiscoveryBatch()
        try:
            index = self.fetch(source.url)
            index_artifact = self.artifacts.write_raw_text(
                "discover", f"source-{source.source_id}.html", index.text
            )
            parsed = parse_index(index.text, index.url)
        except Exception as error:
            batch.source_errors.append(
                {"source_id": source.source_id, "url": source.url, "error": repr(error)}
            )
            return batch
        for link, title in parsed.article_links:
            try:
                page = self.fetch(link)
                canonical = canonicalize_url(page.url)
                artifact = self.artifacts.write_raw_text(
                    "discover", f"article-{stable_hash(canonical)[:20]}.html", page.text
                )
                published = publication_date(page.text, canonical)
                errors: list[str] = []
            except Exception as error:
                canonical = canonicalize_url(link)
                published_date = date_from_url(canonical)
                published = (
                    datetime.combine(published_date, time.min, tzinfo=timezone.utc)
                    if published_date
                    else None
                )
                artifact = index_artifact
                errors = [f"article_fetch_failed:{type(error).__name__}"]
            if published and (
                published.date() < since
                or (until is not None and published.date() > until)
            ):
                continue
            if published is None:
                errors.append("publication_date_missing")
            record_status = RecordStatus.REVIEW if errors else RecordStatus.VALID
            cid = candidate_id(self.name, "", canonical)
            candidate = DiscoveryCandidate(
                candidate_id=cid,
                run_id=run_id,
                provider=self.name,
                discovered_url=link,
                resolved_url=canonical,
                canonical_url=canonical,
                title=title,
                source_id=source.source_id,
                source_name=source.name,
                source_domain=source.domain,
                published_at=published,
                raw_artifact_path=artifact["path"],
                raw_artifact_hash=artifact["sha256"],
                record_status=record_status,
                validation_errors=errors,
            )
            batch.candidates.append(candidate)
            if record_status == RecordStatus.REVIEW:
                batch.reviews.append(_candidate_review(candidate, "candidate_metadata_invalid"))
        return batch


class FeedRegistry:
    def __init__(
        self,
        state: StateStore,
        artifacts: ArtifactStore,
        fetch: Callable[[str], FetchResponse] | None = None,
    ):
        self.state = state
        self.artifacts = artifacts
        self.fetch = fetch or HttpFetcher()

    def discover_for_source(self, source: CuratedSource, html: str) -> list[str]:
        parsed = parse_index(html, source.url)
        candidates = [*parsed.feed_links, *(urljoin(source.url, path) for path in COMMON_FEED_PATHS)]
        out: list[str] = []
        for value in candidates:
            try:
                url = canonicalize_url(value)
            except ValueError:
                continue
            if same_registrable_domain(url, source.url) and url not in out:
                out.append(url)
        return out

    def validate_and_store(self, source: CuratedSource, url: str, method: str) -> tuple[FeedStatus, list[dict]]:
        url = canonicalize_url(url)
        prior = next((row for row in self.state.feeds() if row["url"] == url), None)
        feed_id = prior["feed_id"] if prior else stable_uuid("feed", url)
        failures = int(prior["consecutive_failures"]) if prior else 0
        now = datetime.now(timezone.utc)
        try:
            response = self.fetch(url)
            parsed = feedparser.parse(response.content)
            entries, problems = _valid_feed_entries(parsed.entries, source.url)
            if not entries:
                raise ValueError("feed has no valid same-domain article entries")
            if problems and len(problems) > len(entries):
                status = FeedStatus.QUARANTINED
                error = f"predominantly_invalid_entries:{Counter(problems).most_common()}"
            else:
                status = FeedStatus.ACTIVE
                error = ""
            latest = max((item["published_at"] for item in entries if item["published_at"]), default=now)
            failures = 0
            chain = [*response.history, response.url]
            artifact = self.artifacts.write_raw_text(
                "discover-rss", f"feed-{stable_hash(url)[:20]}.xml", response.text
            )
            for entry in entries:
                entry["feed_url"] = url
                entry["raw_artifact_path"] = artifact["path"]
                entry["raw_artifact_hash"] = artifact["sha256"]
        except Exception as exc:
            failures += 1
            latest = parse_datetime(prior["last_valid_item_at"]) if prior and prior["last_valid_item_at"] else None
            inactive_days = (now - latest).days if latest else 999
            status = feed_status_after_failure(failures, inactive_days)
            error = f"{type(exc).__name__}:{exc}"
            chain = []
            entries = []
        self.state.upsert_feed(
            feed_id,
            source.source_id,
            url,
            status.value,
            method,
            redirect_chain=chain,
            consecutive_failures=failures,
            last_valid_item_at=latest.isoformat() if latest else None,
            last_checked_at=now.isoformat(),
            validation_error=error,
        )
        return status, entries

    def candidates(
        self,
        run_id: str,
        source: CuratedSource,
        entries: Iterable[dict],
        since: date,
        until: date | None = None,
    ) -> DiscoveryBatch:
        batch = DiscoveryBatch()
        for entry in entries:
            published = entry["published_at"]
            if published and (
                published.date() < since
                or (until is not None and published.date() > until)
            ):
                continue
            errors = [] if published else ["publication_date_missing"]
            status = RecordStatus.REVIEW if errors else RecordStatus.VALID
            raw_provider_id = entry.get("provider_id", "")
            feed_identity = f"{entry.get('feed_url', '')}#{raw_provider_id}" if raw_provider_id else ""
            cid = candidate_id("rss", feed_identity, entry["url"])
            candidate = DiscoveryCandidate(
                candidate_id=cid,
                run_id=run_id,
                provider="rss",
                provider_id=feed_identity,
                discovered_url=entry["url"],
                resolved_url=entry["url"],
                canonical_url=entry["url"],
                title=entry.get("title", ""),
                source_id=source.source_id,
                source_name=source.name,
                source_domain=source.domain,
                published_at=published,
                raw_artifact_path=entry.get("raw_artifact_path", ""),
                raw_artifact_hash=entry.get("raw_artifact_hash", ""),
                record_status=status,
                validation_errors=errors,
                metadata={"feed_url": entry.get("feed_url", "")},
            )
            self.state.save_candidate(candidate)
            batch.candidates.append(candidate)
            if errors:
                review = _candidate_review(candidate, "rss_item_undated")
                self.state.add_review(review)
                batch.reviews.append(review)
        return batch


def feed_status_after_failure(consecutive_failures: int, inactive_days: int) -> FeedStatus:
    if consecutive_failures >= 7 or inactive_days >= 30:
        return FeedStatus.DISABLED
    if consecutive_failures >= 3 or inactive_days >= 14:
        return FeedStatus.DEGRADED
    return FeedStatus.PENDING


def _valid_feed_entries(entries: Iterable, source_url: str) -> tuple[list[dict], list[str]]:
    valid: list[dict] = []
    problems: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        link = str(entry.get("link") or "").strip()
        if not link:
            problems.append("missing_link")
            continue
        try:
            url = canonicalize_url(link)
        except ValueError:
            problems.append("invalid_url")
            continue
        if not same_registrable_domain(url, source_url):
            problems.append("off_domain")
            continue
        if url in seen:
            problems.append("duplicate")
            continue
        seen.add(url)
        struct_time = entry.get("published_parsed") or entry.get("updated_parsed")
        published = (
            datetime(
                struct_time.tm_year,
                struct_time.tm_mon,
                struct_time.tm_mday,
                tzinfo=timezone.utc,
            )
            if struct_time
            else parse_datetime(entry.get("published") or entry.get("updated"))
        )
        valid.append(
            {
                "url": url,
                "title": str(entry.get("title") or ""),
                "provider_id": str(entry.get("id") or ""),
                "published_at": published,
            }
        )
    return valid, problems


def _json_ld_dates(value: object) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in {"datepublished", "datecreated", "uploaddate"} and isinstance(item, str):
                out.append(item)
            else:
                out.extend(_json_ld_dates(item))
    elif isinstance(value, list):
        for item in value:
            out.extend(_json_ld_dates(item))
    return out


def _candidate_review(candidate: DiscoveryCandidate, reason: str) -> ReviewItem:
    return ReviewItem(
        review_id=stable_uuid("review", candidate.run_id, "discover", candidate.candidate_id, reason),
        run_id=candidate.run_id,
        stage="discover",
        record_type="discovery_candidate",
        record_id=candidate.candidate_id,
        reason_code=reason,
        validation_errors=candidate.validation_errors,
        raw_artifact_path=candidate.raw_artifact_path,
    )
