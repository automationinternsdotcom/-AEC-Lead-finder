"""Tests for today's two Excel deliverables."""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pipeline.cli import export_test_deliverables as cli


class TestExportTestDeliverables(unittest.TestCase):
    def test_writes_qualified_xlsx_when_enriched_artifact_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "aether-cleaning-az" / "run-1"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            _write_pattern_fixture(artifacts / "pattern.json")

            rc = cli.main(["--run-dir", str(run_dir)])

            self.assertEqual(rc, 0)
            self.assertTrue((run_dir / "deliverables" / "codex-qualified-leads.xlsx").exists())
            self.assertFalse((run_dir / "deliverables" / "grok-enriched-leads.xlsx").exists())

    def test_writes_qualified_and_enriched_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "aether-cleaning-az" / "run-1"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            _write_pattern_fixture(artifacts / "pattern.json")
            (artifacts / "enriched_leads.json").write_text(
                json.dumps({
                    "schema_version": "artifact_envelope.v1",
                    "campaign_id": "aether-cleaning-az",
                    "run_id": "run-1",
                    "stage": "enrich",
                    "records": [{
                        "mode": "fast",
                        "article": {
                            "title": "Mesa project opens",
                            "company_name": "Mesa Owner LLC",
                            "url": "https://example.com/mesa",
                            "priority": "high",
                            "signal_type": "opening",
                            "property_type": "retail",
                            "city": "Mesa",
                            "confidence": 0.92,
                        },
                        "leads": [
                            {"name": "Jane Doe", "title": "COO", "email": "jane@example.com", "phone": "480-555-1212"},
                            {"name": "Pat Roe", "title": "Facilities", "email": None, "phone": None},
                        ],
                    }],
                }),
                encoding="utf-8",
            )

            rc = cli.main(["--run-dir", str(run_dir)])

            self.assertEqual(rc, 0)
            qualified = run_dir / "deliverables" / "codex-qualified-leads.xlsx"
            enriched = run_dir / "deliverables" / "grok-enriched-leads.xlsx"
            self.assertTrue(qualified.exists())
            self.assertTrue(enriched.exists())
            self.assertIn("Mesa project opens", _sheet_xml(qualified))
            enriched_xml = _sheet_xml(enriched)
            self.assertIn("Jane Doe", enriched_xml)
            self.assertIn("Pat Roe", enriched_xml)


def _sheet_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("xl/worksheets/sheet1.xml").decode("utf-8")


def _write_pattern_fixture(path: Path) -> None:
    path.write_text(
        json.dumps({
            "schema_version": "artifact_envelope.v1",
            "campaign_id": "aether-cleaning-az",
            "run_id": "run-1",
            "stage": "pattern",
            "records": [{
                "qualified": True,
                "score": 91,
                "raw": {
                    "title": "Mesa project opens",
                    "company_name": "Mesa Owner LLC",
                    "url": "https://example.com/mesa",
                    "priority": "high",
                    "signal_type": "opening",
                    "property_type": "retail",
                    "city": "Mesa",
                    "confidence": 0.92,
                    "filter_reason": "Opening signal.",
                },
            }],
        }),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
