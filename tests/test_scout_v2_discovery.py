"""Discovery adapter, feed lifecycle, provider, and dedup contracts."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.artifacts import ArtifactStore  # noqa: E402
from v2.contracts import FeedStatus, RecordStatus  # noqa: E402
from v2.dedup import DedupContractError, validate_fuzzy_groups  # noqa: E402
from v2.discovery import (  # noqa: E402
    CuratedSiteAdapter,
    CuratedSource,
    FeedRegistry,
    feed_status_after_failure,
    parse_index,
    publication_date,
)
from v2.http import FetchResponse  # noqa: E402
from v2.providers import (  # noqa: E402
    ApifyFacebookAdapter,
    NewsApiAdapter,
    ProviderPreflightError,
)
from v2.state import StateStore  # noqa: E402


def response(url: str, text: str) -> FetchResponse:
    return FetchResponse(url=url, content=text.encode())


def test_parse_index_finds_articles_and_same_domain_feed():
    parsed = parse_index(
        """
        <link rel="alternate" type="application/rss+xml" href="/feed.xml">
        <a href="/2026/08/28/large-commercial-project-opens">A large commercial project opens in Phoenix</a>
        <a href="/about">About our publication and team</a>
        <link rel="alternate" type="application/rss+xml" href="https://spam.test/feed">
        """,
        "https://news.example.com",
    )
    assert parsed.article_links == [
        (
            "https://news.example.com/2026/08/28/large-commercial-project-opens",
            "A large commercial project opens in Phoenix",
        )
    ]
    assert parsed.feed_links == ["https://news.example.com/feed.xml"]


def test_publication_date_prefers_structured_metadata_to_url():
    found = publication_date(
        '<meta property="article:published_time" content="2026-08-28T15:30:00Z">',
        "https://example.com/2020/01/01/story",
    )
    assert found and found.date() == date(2026, 8, 28)


def test_curated_adapter_persists_valid_and_undated_review(tmp_path):
    source = CuratedSource(
        source_id="source-1",
        name="Example News",
        url="https://example.com",
        domain="example.com",
    )
    pages = {
        "https://example.com/": """
            <a href="/2026/08/28/a-new-commercial-project-opens">A new commercial project opens in Phoenix today</a>
            <a href="/news/another-commercial-development-story">Another commercial development story in Arizona</a>
        """,
        "https://example.com/2026/08/28/a-new-commercial-project-opens": (
            '<meta property="article:published_time" content="2026-08-28T08:00:00Z">'
        ),
        "https://example.com/news/another-commercial-development-story": "<html>no date</html>",
    }

    def fetch(url: str) -> FetchResponse:
        canonical = url if url != "https://example.com" else "https://example.com/"
        return response(canonical, pages[canonical])

    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    artifacts = ArtifactStore(tmp_path / "results", "2026-08-28", "run-1", store)
    batch = CuratedSiteAdapter([source], store, artifacts, fetch=fetch, workers=1).discover(
        "run-1", date(2026, 8, 27)
    )

    assert [item.record_status for item in batch.candidates] == [
        RecordStatus.VALID,
        RecordStatus.REVIEW,
    ]
    assert len(batch.reviews) == 1
    assert len(store.candidates_for_run("run-1")) == 2


def test_feed_validation_and_health_thresholds(tmp_path):
    source = CuratedSource(
        source_id="source-1",
        name="Example",
        url="https://example.com",
        domain="example.com",
    )
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><guid>one</guid><title>Opening</title><link>https://example.com/news/opening</link>
      <pubDate>Fri, 28 Aug 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    store.upsert_source(source.source_id, source.name, source.url, source.domain)
    artifacts = ArtifactStore(tmp_path / "results", "2026-08-28", "run-1", store)
    registry = FeedRegistry(store, artifacts, fetch=lambda url: response(url, feed))

    status, entries = registry.validate_and_store(source, "https://example.com/feed.xml", "autodiscovery")
    assert status == FeedStatus.ACTIVE
    assert entries[0]["provider_id"] == "one"
    assert feed_status_after_failure(2, 1) == FeedStatus.PENDING
    assert feed_status_after_failure(3, 1) == FeedStatus.DEGRADED
    assert feed_status_after_failure(7, 1) == FeedStatus.DISABLED


def test_provider_preflight_and_bounded_results():
    with pytest.raises(ProviderPreflightError, match="NEWSAPI_AI_API_KEY"):
        NewsApiAdapter(api_key="").preflight()

    calls = []

    def post_json(url, payload, timeout):
        calls.append(payload)
        return {
            "articles": {
                "results": [
                    {
                        "uri": f"id-{len(calls)}",
                        "url": f"https://example.com/{len(calls)}",
                        "title": "Arizona opening",
                        "date": "2026-08-28",
                    }
                ],
                "pages": 1,
            }
        }

    records = NewsApiAdapter(api_key="key", max_pages=0, post_json=post_json).discover(
        date(2026, 8, 27), date(2026, 8, 28)
    )
    assert len(calls) == 10
    assert len(records) == 10

    rows = [{"id": str(i), "url": f"https://facebook.com/{i}", "text": "opening"} for i in range(30)]
    apify = ApifyFacebookAdapter(
        token="token",
        actor_id="actor",
        run_actor=lambda token, actor, payload, timeout: rows,
    )
    assert len(apify.discover(date(2026, 8, 27), date(2026, 8, 28))) == 20


def test_fuzzy_dedup_requires_exact_coverage():
    assert validate_fuzzy_groups(
        ["a", "b"], [{"kept_id": "a", "member_ids": ["a", "b"]}]
    )[0].member_ids == ("a", "b")
    with pytest.raises(DedupContractError, match=r"missing=\['b'\]"):
        validate_fuzzy_groups(["a", "b"], [{"kept_id": "a", "member_ids": ["a"]}])
    with pytest.raises(DedupContractError, match=r"unknown=\['x'\]"):
        validate_fuzzy_groups(["a"], [{"kept_id": "a", "member_ids": ["a", "x"]}])
