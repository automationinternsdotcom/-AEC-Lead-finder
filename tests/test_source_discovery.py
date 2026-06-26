"""Tests for Gemini source discovery normalization."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from pipeline.contracts import ArtifactEnvelope
from pipeline.source_discovery import (
    SourceCandidate,
    gemini_discovery_to_artifact,
    parse_gemini_discovery_transcript,
    records_for_fetch,
)
from pipeline.spec import load_campaign_spec_v2

FIXTURES = Path(__file__).parent / "fixtures"


def _source(url: str, **overrides) -> dict:
    base = {
        "url": url,
        "source_name": "Example",
        "source_type": "article",
        "title": "Example article",
        "reason": "It matches the campaign trigger signal.",
        "confidence": 0.9,
        "suggested_pattern_type": "event_signal",
    }
    base.update(overrides)
    return base


class TestSourceCandidate(unittest.TestCase):
    def test_canonicalizes_and_hashes_url(self):
        candidate = SourceCandidate.model_validate(
            _source("https://Example.com/a/?utm_source=x#frag")
        )
        self.assertEqual(candidate.canonical_url, "https://example.com/a")
        self.assertIsNotNone(candidate.url_hash)

    def test_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            SourceCandidate.model_validate(_source("javascript:alert(1)"))

    def test_accepts_common_provider_key_names(self):
        candidate = SourceCandidate.model_validate({
            "url": "https://example.com/a",
            "source_name": "Example",
            "type": "directory",
            "why": "Relevant directory.",
            "confidence": 0.8,
            "pattern_type": "entity_aggregation",
        })
        self.assertEqual(candidate.source_type, "directory")
        self.assertEqual(candidate.suggested_pattern_type, "entity_aggregation")

    def test_normalizes_markdown_google_search_url(self):
        candidate = SourceCandidate.model_validate(_source(
            "[https://www.tempe.gov/government/economic-development/news]"
            "(https://www.google.com/search?q=https://www.tempe.gov/government/economic-development/news)",
            source_name="City of Tempe",
            title="Tempe Economic Development News",
        ))
        self.assertEqual(
            candidate.url,
            "https://www.tempe.gov/government/economic-development/news",
        )
        self.assertEqual(
            candidate.canonical_url,
            "https://www.tempe.gov/government/economic-development/news",
        )

    def test_unwraps_google_search_url(self):
        candidate = SourceCandidate.model_validate(_source(
            "https://www.google.com/search?q=https%3A%2F%2Fwww.phoenix.gov%2Fpdd%2Fdevelopment%2Fpermits",
            source_type="permit_listing",
        ))
        self.assertEqual(
            candidate.url,
            "https://www.phoenix.gov/pdd/development/permits",
        )

    def test_accepts_campaign_source_types_from_prompt(self):
        for source_type in ("rss_feed", "atom_feed", "sitemap", "permit_listing", "market_report"):
            candidate = SourceCandidate.model_validate(_source(
                "https://example.com/source",
                source_type=source_type,
            ))
            self.assertEqual(candidate.source_type, source_type)


class TestGeminiDiscoveryParsing(unittest.TestCase):
    def test_saved_fixture_has_expected_rejections(self):
        spec = load_campaign_spec_v2()
        transcript = (FIXTURES / "gemini_discovery_transcript.json").read_text(encoding="utf-8")
        result = parse_gemini_discovery_transcript(transcript, spec)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(len(result.rejected), 3)
        self.assertEqual(result.rejected[0]["reason"], "duplicate_url")

    def test_parses_filters_and_deduplicates_sources(self):
        spec = load_campaign_spec_v2()
        transcript = json.dumps({
            "sources": [
                _source("https://example.com/a?utm_source=x"),
                _source("https://example.com/a"),
                _source("https://example.com/low", confidence=0.2),
                _source("ftp://example.com/b"),
            ]
        })
        result = parse_gemini_discovery_transcript(transcript, spec)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].canonical_url, "https://example.com/a")
        reasons = [item["reason"] for item in result.rejected]
        self.assertEqual(reasons[:2], ["duplicate_url", "below_confidence_threshold"])
        self.assertTrue(reasons[2].startswith("invalid_source:"))

    def test_parses_prose_wrapped_json_output(self):
        spec = load_campaign_spec_v2()
        transcript = "Here are the sources I found:\n" + json.dumps({
            "sources": [_source("https://example.com/live")]
        }) + "\nLet me know if you want more."
        result = parse_gemini_discovery_transcript(transcript, spec)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].canonical_url, "https://example.com/live")

    def test_artifact_and_fetch_rows(self):
        spec = load_campaign_spec_v2()
        result = parse_gemini_discovery_transcript(
            json.dumps({"sources": [_source("https://example.com/a")]}),
            spec,
        )
        artifact = gemini_discovery_to_artifact(
            result,
            campaign_id=spec.campaign_id,
            run_id="run",
        )
        self.assertEqual(artifact.stage, "discover")
        rows = records_for_fetch(artifact)
        self.assertEqual(rows[0]["url"], "https://example.com/a")
        self.assertEqual(rows[0]["source"], "Example")

    def test_records_for_fetch_requires_discover_artifact(self):
        with self.assertRaises(ValueError):
            records_for_fetch(ArtifactEnvelope(
                campaign_id="campaign",
                run_id="run",
                stage="fetch",
                records=[],
            ))

    def test_malformed_transcript_raises(self):
        spec = load_campaign_spec_v2()
        transcript = (FIXTURES / "bad_gemini_discovery_transcript.txt").read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_gemini_discovery_transcript(transcript, spec)


if __name__ == "__main__":
    unittest.main()
