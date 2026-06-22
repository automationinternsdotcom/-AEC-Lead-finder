"""Tests for Phase 2 destination preview adapters."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from pipeline.contracts import ArtifactEnvelope
from pipeline.delivery import delivery_records_from_artifact
from pipeline.destinations import ExcelDestination, PipedriveDestination, destination_for
from pipeline.destinations.base import DeliveryNotApproved, DeliveryRecord
from pipeline.spec import DestinationV2


class TestDeliveryRecords(unittest.TestCase):
    def test_delivery_records_from_pattern_artifact(self):
        artifact = ArtifactEnvelope(
            campaign_id="campaign",
            run_id="run",
            stage="pattern",
            records=[
                {
                    "entity_name": "Acme",
                    "score": 88,
                    "raw": {"title": "Acme opens property", "priority": "high"},
                }
            ],
        )
        records = delivery_records_from_artifact(artifact)
        self.assertEqual(records[0].title, "Acme opens property")
        self.assertEqual(records[0].company_name, "Acme")
        self.assertEqual(records[0].score, 88)


class TestExcelDestination(unittest.TestCase):
    def test_preview_writes_xlsx(self):
        records = [DeliveryRecord(title="Lead", company_name="Acme", score=90)]
        with tempfile.TemporaryDirectory() as tmp:
            preview = ExcelDestination().preview(
                records,
                output_dir=Path(tmp),
                run_id="run",
            )
            path = Path(preview.output_path)
            self.assertTrue(path.exists())
            with zipfile.ZipFile(path) as zf:
                sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("Lead", sheet)
        self.assertIn("Acme", sheet)
        self.assertEqual(preview.record_count, 1)


class TestDestinationRegistry(unittest.TestCase):
    def test_returns_excel_destination(self):
        adapter = destination_for(DestinationV2(type="excel"))
        self.assertIsInstance(adapter, ExcelDestination)

    def test_pipedrive_delivery_requires_approval_and_is_guarded(self):
        adapter = PipedriveDestination()
        with self.assertRaises(DeliveryNotApproved):
            adapter.deliver([], approved=False)
        with self.assertRaises(NotImplementedError):
            adapter.deliver([], approved=True)


if __name__ == "__main__":
    unittest.main()
