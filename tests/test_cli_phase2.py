"""CLI smoke tests for Phase 2A helpers."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.cli import init_run as init_run_cli
from pipeline.cli import fetch_discovered as fetch_discovered_cli
from pipeline.cli import next_action as next_action_cli
from pipeline.cli import parse_gemini_discovery as parse_gemini_discovery_cli
from pipeline.cli import parse_transcript as parse_transcript_cli
from pipeline.cli import preview_delivery as preview_delivery_cli
from pipeline.cli import run_pattern as run_pattern_cli
from pipeline.cli import validate_artifact as validate_artifact_cli
from pipeline.cli import validate_spec as validate_spec_cli
from pipeline.contracts import ArtifactEnvelope
from pipeline.run_state import init_run


class TestPhase2Cli(unittest.TestCase):
    def test_validate_spec_outputs_resolved_v2_json(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = validate_spec_cli.main([])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["schema_version"], "campaign_spec.v2")
        self.assertEqual(data["identity"]["campaign_id"], "aether-cleaning-az")

    def test_init_run_cli_prints_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("pipeline.cli.init_run.init_run", wraps=lambda campaign, run_id=None: init_run(campaign, run_id=run_id, runs_dir=Path(tmp))), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = init_run_cli.main(["--run-id", "cli-run"])
        self.assertEqual(rc, 0)
        self.assertIn("cli-run", stdout.getvalue())

    def test_next_action_cli_outputs_discover(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = init_run(run_id="cli-run", runs_dir=Path(tmp))
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = next_action_cli.main([str(run_dir)])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["stage"], "discover")

    def test_validate_artifact_cli_outputs_summary(self):
        envelope = ArtifactEnvelope(
            campaign_id="campaign",
            run_id="run",
            stage="fetch",
            records=[{"url": "https://example.com"}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.json"
            path.write_text(json.dumps(envelope.model_dump(mode="json")), encoding="utf-8")
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = validate_artifact_cli.main([str(path)])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertTrue(data["ok"])
        self.assertEqual(data["record_count"], 1)

    def test_run_pattern_cli_outputs_pattern_artifact(self):
        records = [
            {
                "entity_name": "Desert Ridge Property Management LLC",
                "source": "county_roster",
                "signal": "portfolio ownership",
                "confidence": 0.9,
                "domain": "desertridgepm.com",
                "city": "Phoenix",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = run_pattern_cli.main([
                    str(path),
                    "--pattern",
                    "entity_aggregation",
                    "--run-id",
                    "cli-run",
                ])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["stage"], "pattern")
        self.assertEqual(data["metadata"]["pattern_type"], "entity_aggregation")
        self.assertEqual(data["metadata"]["record_count"], 1)

    def test_parse_transcript_cli_outputs_enrichment_artifact(self):
        transcript = """
1. Jane Doe
Current Title: COO, Acme
Professional Email: jane@acme.com
Direct Phone: 480-555-0100
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.txt"
            path.write_text(transcript, encoding="utf-8")
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = parse_transcript_cli.main([
                    str(path),
                    "--company-name",
                    "Acme",
                    "--run-id",
                    "cli-run",
                ])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["stage"], "enrich")
        self.assertEqual(data["records"][0]["lead"]["name"], "Jane Doe")

    def test_preview_delivery_cli_writes_excel_preview(self):
        artifact = ArtifactEnvelope(
            campaign_id="aether-cleaning-az",
            run_id="cli-run",
            stage="pattern",
            records=[{"entity_name": "Acme", "score": 91, "raw": {"title": "Lead"}}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            artifact_path = Path(tmp) / "artifact.json"
            artifact_path.write_text(json.dumps(artifact.model_dump(mode="json")), encoding="utf-8")
            out_dir = Path(tmp) / "preview"
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = preview_delivery_cli.main([
                    str(artifact_path),
                    "--run-id",
                    "cli-run",
                    "--output-dir",
                    str(out_dir),
                ])
            data = json.loads(stdout.getvalue())
            preview_exists = Path(data["output_path"]).exists()
        self.assertEqual(rc, 0)
        self.assertEqual(data["destination_type"], "excel")
        self.assertEqual(data["record_count"], 1)
        self.assertTrue(preview_exists)

    def test_parse_gemini_discovery_cli_outputs_discover_artifact(self):
        transcript = {
            "sources": [
                {
                    "url": "https://example.com/article?utm_source=x",
                    "source_name": "Example",
                    "source_type": "article",
                    "title": "Example article",
                    "reason": "Matches the campaign signal.",
                    "confidence": 0.9,
                    "suggested_pattern_type": "event_signal",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gemini.txt"
            path.write_text(json.dumps(transcript), encoding="utf-8")
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = parse_gemini_discovery_cli.main([
                    str(path),
                    "--run-id",
                    "cli-run",
                ])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["stage"], "discover")
        self.assertEqual(data["records"][0]["canonical_url"], "https://example.com/article")

    def test_fetch_discovered_cli_prints_fetch_rows_without_db(self):
        artifact = ArtifactEnvelope(
            campaign_id="aether-cleaning-az",
            run_id="cli-run",
            stage="discover",
            records=[
                {
                    "url": "https://example.com/a",
                    "canonical_url": "https://example.com/a",
                    "url_hash": "hash-a",
                    "source_name": "Example",
                    "title": "A",
                }
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discover.json"
            path.write_text(json.dumps(artifact.model_dump(mode="json")), encoding="utf-8")
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = fetch_discovered_cli.main([str(path), "--no-db"])
        self.assertEqual(rc, 0)
        rows = json.loads(stdout.getvalue())
        self.assertEqual(rows[0]["url_hash"], "hash-a")
        self.assertEqual(rows[0]["source"], "Example")


if __name__ == "__main__":
    unittest.main()
