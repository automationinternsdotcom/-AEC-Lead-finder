"""Tests for discovered source classification and expansion."""
from __future__ import annotations

import unittest
from unittest.mock import patch

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
