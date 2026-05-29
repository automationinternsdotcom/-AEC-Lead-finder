from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from schema import Evidence, ExtractionResult, LeadRecord, Qualification
from pipeline import db, dedupe, qualify, score


class RuleTests(unittest.TestCase):
    def test_residential_only_extraction_is_rejected(self) -> None:
        extraction = ExtractionResult(
            source_url="https://example.com/story",
            qualification=Qualification(
                is_qualified=False,
                city="Phoenix",
                reason="residential_only",
                recency_status="announced",
            ),
            confidence=0.9,
        )

        status, reason, rejection = qualify.qualify(extraction)

        self.assertEqual(status, "rejected")
        self.assertEqual(reason, "residential_only")
        self.assertEqual(rejection, "residential_only")

    def test_phoenix_industrial_opening_qualifies(self) -> None:
        extraction = _qualified_extraction()

        status, reason, rejection = qualify.qualify(extraction)

        self.assertEqual(status, "qualified")
        self.assertEqual(reason, "qualified_phoenix_metro_commercial_property")
        self.assertIsNone(rejection)

    def test_missing_contact_still_routes_to_review_when_score_is_strong(self) -> None:
        breakdown = score.score_lead(_qualified_extraction())

        self.assertEqual(breakdown.score, 70)
        self.assertEqual(breakdown.route, "review")
        self.assertTrue(any(line.signal == "missing_contact" for line in breakdown.lines))

    def test_local_dedupe_detects_existing_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = db.connect(Path(temp_dir) / "test.db")
            first = _lead("lead-1", "https://example.com/story")
            second = _lead("lead-2", "https://example.com/story")

            db.save_lead(conn, first)
            result = dedupe.check_local_duplicates(conn, second)
            conn.close()

        self.assertEqual(result.outcome, "exact_duplicate")
        self.assertEqual(result.existing_lead_id, "lead-1")


def _qualified_extraction() -> ExtractionResult:
    return ExtractionResult(
        property_name="Aether Industrial Center",
        address="123 Loop 101, Phoenix, AZ",
        property_type="industrial",
        opening_date_or_status="opening next month",
        description="New industrial facility opening in Phoenix.",
        source_url="https://example.com/story",
        qualification=Qualification(
            is_qualified=True,
            city="Phoenix",
            reason="new Phoenix industrial opening",
            recency_status="within_90_days_opening",
        ),
        confidence=0.9,
        evidence=[
            Evidence(field="property_name", quote="Aether Industrial Center"),
            Evidence(field="opening_date_or_status", quote="opening next month"),
        ],
    )


def _lead(lead_id: str, source_url: str) -> LeadRecord:
    extraction = _qualified_extraction().model_copy(update={"source_url": source_url})
    lead = LeadRecord(
        lead_id=lead_id,
        source_id="source-1",
        source_site="Example",
        source_url=source_url,
        property_name=extraction.property_name,
        address=extraction.address,
        property_type=extraction.property_type,
        opening_date_or_status=extraction.opening_date_or_status,
        qualification_status="qualified",
        qualification_reason="qualified_phoenix_metro_commercial_property",
        extraction=extraction,
    )
    lead.dedupe_key = dedupe.build_dedupe_key(lead)
    return lead


if __name__ == "__main__":
    unittest.main()
