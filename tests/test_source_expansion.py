"""Tests for discovered source classification and expansion."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from pipeline.contracts import ArtifactEnvelope
from pipeline.source_expansion import expand_discovery_artifact
from pipeline.url_classifier import classify_url


class TestUrlClassifier(unittest.TestCase):
    def test_honors_supported_hint(self):
        self.assertEqual(classify_url("https://example.com/anything", "permit_listing"), "permit_listing")

    def test_classifies_common_shapes(self):
        self.assertEqual(classify_url("https://example.com/feed"), "rss_feed")
        self.assertEqual(classify_url("https://example.com/sitemap.xml"), "sitemap")
        self.assertEqual(classify_url("https://example.com/market-report/phoenix"), "market_report")
        self.assertEqual(classify_url("https://example.com/news/item"), "article")

    def test_overrides_article_hint_for_listing_pages(self):
        self.assertEqual(
            classify_url(
                "https://azbigmedia.com/category/real-estate/commercial-real-estate/",
                "article",
            ),
            "source_listing",
        )
        self.assertEqual(
            classify_url("https://www.mortenson.com/projects?market=phoenix", "article"),
            "source_listing",
        )


class TestSourceExpansion(unittest.TestCase):
    def test_direct_article_expands_to_fetch_row_with_namespaced_hash(self):
        artifact = ArtifactEnvelope(
            campaign_id="aether-cleaning-az",
            run_id="run",
            stage="discover",
            records=[
                {
                    "url": "https://example.com/article?utm_source=x",
                    "canonical_url": "https://example.com/article",
                    "source_name": "Example",
                    "source_type": "article",
                    "title": "Example article",
                }
            ],
        )
        rows, classified = expand_discovery_artifact(artifact, dedupe_namespace="cleaning")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].url, "https://example.com/article")
        self.assertEqual(rows[0].source_type, "article")
        self.assertEqual(classified[0].expanded_count, 1)

    def test_feed_expands_entries(self):
        artifact = ArtifactEnvelope(
            campaign_id="aether-cleaning-az",
            run_id="run",
            stage="discover",
            records=[
                {
                    "url": "https://example.com/feed",
                    "canonical_url": "https://example.com/feed",
                    "source_name": "Example Feed",
                    "source_type": "rss_feed",
                    "title": "Feed",
                }
            ],
        )
        parsed_feed = type("ParsedFeed", (), {
            "entries": [
                {"link": "https://example.com/a?utm_source=x", "title": "A"},
                {"link": "https://example.com/b", "title": "B"},
            ]
        })()
        with patch("pipeline.source_expansion.feedparser.parse", return_value=parsed_feed):
            rows, classified = expand_discovery_artifact(artifact, dedupe_namespace="cleaning")
        self.assertEqual([row.url for row in rows], ["https://example.com/a", "https://example.com/b"])
        self.assertEqual(classified[0].expanded_count, 2)

    def test_source_listing_extracts_candidate_links(self):
        artifact = ArtifactEnvelope(
            campaign_id="aether-cleaning-az",
            run_id="run",
            stage="discover",
            records=[
                {
                    "url": "https://example.com/news",
                    "canonical_url": "https://example.com/news",
                    "source_name": "Example News",
                    "source_type": "article",
                    "title": "News",
                }
            ],
        )
        response = Mock()
        response.text = """
        <html><body>
          <a href="/news/project-breaks-ground">Project breaks ground</a>
          <a href="/category/real-estate">Category</a>
          <a href="https://other.example.com/news/outside">Outside</a>
          <a href="/news/project-breaks-ground?utm_source=x">Duplicate</a>
          <a href="/news/tenant-opening">Tenant opening</a>
        </body></html>
        """
        response.raise_for_status.return_value = None
        client = Mock()
        client.get.return_value = response
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=None)

        with patch("pipeline.source_expansion.util.make_http_client", return_value=client):
            rows, classified = expand_discovery_artifact(artifact, dedupe_namespace="cleaning")

        self.assertEqual(classified[0].source_type, "source_listing")
        self.assertEqual(
            [row.url for row in rows],
            [
                "https://example.com/news/project-breaks-ground",
                "https://example.com/news/tenant-opening",
            ],
        )
        self.assertEqual([row.source_type for row in rows], ["source_listing", "source_listing"])

    def test_homepage_is_classified_but_not_expanded(self):
        artifact = ArtifactEnvelope(
            campaign_id="aether-cleaning-az",
            run_id="run",
            stage="discover",
            records=[
                {
                    "url": "https://example.com",
                    "canonical_url": "https://example.com/",
                    "source_name": "Example",
                    "source_type": "homepage",
                    "title": "Homepage",
                }
            ],
        )
        rows, classified = expand_discovery_artifact(artifact, dedupe_namespace="cleaning")
        self.assertEqual(rows, [])
        self.assertEqual(classified[0].skipped_reason, "unsupported_source_type:homepage")


if __name__ == "__main__":
    unittest.main()
