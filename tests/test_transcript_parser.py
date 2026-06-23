"""Tests for browser/chat transcript parsing."""
from __future__ import annotations

import unittest
from pathlib import Path

from pipeline.grok_parse import is_lead_generic
from pipeline.transcript_parser import (
    enrichment_result_to_artifact,
    extract_first_json_value,
    parse_grok_enrichment_transcript,
)

FIXTURES = Path(__file__).parent / "fixtures"

GROK_TEXT = """
1. Jane Doe
Current Title: COO, Acme Property Management
LinkedIn: https://linkedin.com/in/janedoe
Professional Email: jane.doe@acme.com
Direct Phone: (480) 555-0100
"""


class TestTranscriptParser(unittest.TestCase):
    def test_saved_grok_fixture_parses_two_contacts(self):
        text = (FIXTURES / "grok_enrichment_transcript.txt").read_text(encoding="utf-8")
        result = parse_grok_enrichment_transcript(
            text,
            company_name="Desert Ridge Property Management",
            mode="fast",
        )
        self.assertTrue(result.ok)
        self.assertEqual([lead.name for lead in result.leads], ["Jane Doe", "Alex Rivera"])

    def test_parse_grok_enrichment_transcript(self):
        result = parse_grok_enrichment_transcript(
            GROK_TEXT,
            company_name="Acme",
            mode="fast",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.leads[0].name, "Jane Doe")
        self.assertEqual(result.company_name, "Acme")

    def test_empty_grok_transcript_reports_error(self):
        result = parse_grok_enrichment_transcript("", company_name="Acme")
        self.assertFalse(result.ok)
        self.assertEqual(result.errors, ["no_leads_parsed"])

    def test_generic_contact_fixture_is_detectable(self):
        text = (FIXTURES / "generic_grok_enrichment_transcript.txt").read_text(encoding="utf-8")
        result = parse_grok_enrichment_transcript(text, company_name="Example")
        self.assertTrue(result.ok)
        self.assertTrue(is_lead_generic(result.leads[0]))

    def test_result_to_artifact(self):
        result = parse_grok_enrichment_transcript(GROK_TEXT, company_name="Acme", mode="fast")
        artifact = enrichment_result_to_artifact(
            result,
            campaign_id="campaign",
            run_id="run",
        )
        self.assertEqual(artifact.stage, "enrich")
        self.assertEqual(artifact.records[0]["lead"]["name"], "Jane Doe")
        self.assertEqual(artifact.metadata["provider"], "grok")

    def test_extract_first_json_value_from_fence(self):
        self.assertEqual(
            extract_first_json_value('text\n```json\n{"ok": true}\n```'),
            {"ok": True},
        )


if __name__ == "__main__":
    unittest.main()
