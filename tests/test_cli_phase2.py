"""CLI smoke tests for Phase 2A helpers."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.cli import init_run as init_run_cli
from pipeline.cli import next_action as next_action_cli
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

    def test_next_action_cli_outputs_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = init_run(run_id="cli-run", runs_dir=Path(tmp))
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = next_action_cli.main([str(run_dir)])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["stage"], "fetch")

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


if __name__ == "__main__":
    unittest.main()
