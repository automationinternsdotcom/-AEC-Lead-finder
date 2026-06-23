"""Phase 2E artifact-chain tests for the discovery-first engine."""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pipeline.contracts import ArtifactEnvelope
from pipeline.delivery import delivery_records_from_artifact
from pipeline.destinations import ExcelDestination
from pipeline.patterns import EventSignalPattern
from pipeline.source_discovery import (
    gemini_discovery_to_artifact,
    parse_gemini_discovery_transcript,
    records_for_fetch,
)
from pipeline.transcript_parser import (
    enrichment_result_to_artifact,
    parse_grok_enrichment_transcript,
)
from pipeline.spec import load_campaign_spec_v2
from schema import ExtractedArticle

FIXTURES = Path(__file__).parent / "fixtures"


def _extracted_article() -> dict:
    return ExtractedArticle.model_validate({
        "title": "Phoenix apartment tower reaches lease-up",
        "published_date": "2026-06-01",
        "summary_2sent": "A Phoenix apartment tower reached lease-up after construction completion. The property has 240 units and active operations.",
        "signal_type": "construction",
        "company_name": "Desert Ridge Property Management",
        "company_domain_guess": "desertridgepm.com",
        "property_type": "multifamily",
        "address": "100 N Central Ave, Phoenix, AZ",
        "city": "Phoenix",
        "square_footage": None,
        "dollar_value": None,
        "unit_count": 240,
        "az_relevant": True,
        "confidence": 0.91,
        "priority": "high",
        "filter_reason": "New multifamily lease-up in Aether's Arizona corridor.",
        "service_angle": "Lease-up phase signals need for an asset-preservation partner across 240 doors."
    }).model_dump(mode="json")


class TestPhase2ArtifactChain(unittest.TestCase):
    def test_saved_fixtures_flow_to_preview_xlsx(self):
        spec = load_campaign_spec_v2()
        gemini_text = (FIXTURES / "gemini_discovery_transcript.json").read_text(encoding="utf-8")
        discovery_result = parse_gemini_discovery_transcript(gemini_text, spec)
        discover_artifact = gemini_discovery_to_artifact(
            discovery_result,
            campaign_id=spec.campaign_id,
            run_id="chain-run",
        )

        fetch_rows = records_for_fetch(discover_artifact)
        self.assertEqual(len(fetch_rows), 2)
        self.assertEqual(fetch_rows[0]["url"], "https://example.com/news/phoenix-apartment-tower-lease-up")
        self.assertEqual(discover_artifact.metadata["rejected_count"], 3)

        pattern_result = EventSignalPattern().run([_extracted_article()], spec)
        pattern_artifact = ArtifactEnvelope(
            campaign_id=spec.campaign_id,
            run_id="chain-run",
            stage="pattern",
            records=[record.model_dump(mode="json") for record in pattern_result.records],
            metadata={"pattern_type": pattern_result.pattern_type, "stats": pattern_result.stats},
        )
        self.assertEqual(pattern_artifact.records[0]["qualified"], True)

        grok_text = (FIXTURES / "grok_enrichment_transcript.txt").read_text(encoding="utf-8")
        enrich_result = parse_grok_enrichment_transcript(
            grok_text,
            company_name="Desert Ridge Property Management",
            mode="fast",
        )
        enrich_artifact = enrichment_result_to_artifact(
            enrich_result,
            campaign_id=spec.campaign_id,
            run_id="chain-run",
        )
        self.assertEqual(len(enrich_artifact.records), 2)

        delivery_records = delivery_records_from_artifact(pattern_artifact)
        first_lead = enrich_artifact.records[0]["lead"]
        delivery_records[0].contact_name = first_lead["name"]
        delivery_records[0].contact_title = first_lead["title"]
        delivery_records[0].contact_email = first_lead["email"]
        delivery_records[0].contact_phone = first_lead["phone"]

        with tempfile.TemporaryDirectory() as tmp:
            preview = ExcelDestination().preview(
                delivery_records,
                output_dir=Path(tmp),
                run_id="chain-run",
            )
            with zipfile.ZipFile(preview.output_path) as zf:
                sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertEqual(preview.record_count, 1)
        self.assertIn("Desert Ridge Property Management", sheet)
        self.assertIn("Jane Doe", sheet)


if __name__ == "__main__":
    unittest.main()
