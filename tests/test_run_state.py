"""Tests for Phase 2 run-state and artifact contracts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from pipeline.contracts import ArtifactEnvelope, load_artifact, load_manifest
from pipeline.run_state import init_run, next_action_for_manifest


class TestRunState(unittest.TestCase):
    def test_init_run_writes_expected_directory_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = init_run(run_id="test-run", runs_dir=Path(tmp))

            self.assertTrue((run_dir / "spec.resolved.yaml").exists())
            self.assertTrue((run_dir / "manifest.json").exists())
            self.assertTrue((run_dir / "checkpoints.json").exists())
            for dirname in ("artifacts", "prompts", "transcripts", "quarantine", "previews", "delivery"):
                self.assertTrue((run_dir / dirname).is_dir())

            manifest = load_manifest(run_dir / "manifest.json")

        self.assertEqual(manifest.campaign_id, "aether-cleaning-az")
        self.assertEqual(manifest.run_id, "test-run")
        self.assertEqual(manifest.stages["fetch"].status, "pending")
        self.assertEqual(manifest.stages["pattern"].route, "deterministic_cli")
        self.assertEqual(manifest.stages["qualify"].route, "codex_in_session")
        self.assertTrue(manifest.preview_required)
        self.assertFalse(manifest.live_delivery_allowed)

    def test_next_action_starts_at_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = init_run(run_id="test-run", runs_dir=Path(tmp))
            manifest = load_manifest(run_dir / "manifest.json")
        action = next_action_for_manifest(manifest)
        self.assertEqual(action.status, "ready")
        self.assertEqual(action.stage, "fetch")
        self.assertEqual(action.route, "deterministic_cli")

    def test_next_action_blocks_delivery_before_preview_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = init_run(run_id="test-run", runs_dir=Path(tmp))
            manifest = load_manifest(run_dir / "manifest.json")
        for stage in ("fetch", "extract", "pattern", "qualify", "enrich", "preview"):
            manifest.stages[stage].status = "complete"
        action = next_action_for_manifest(manifest)
        self.assertEqual(action.status, "blocked")
        self.assertEqual(action.stage, "deliver")


class TestArtifactEnvelope(unittest.TestCase):
    def test_artifact_envelope_round_trips(self):
        envelope = ArtifactEnvelope(
            campaign_id="campaign",
            run_id="run",
            stage="fetch",
            records=[{"url": "https://example.com"}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.json"
            path.write_text(json.dumps(envelope.model_dump(mode="json")), encoding="utf-8")
            loaded = load_artifact(path)
        self.assertEqual(loaded.metadata["record_count"], 1)

    def test_metadata_record_count_must_match_records(self):
        with self.assertRaises(ValidationError):
            ArtifactEnvelope(
                campaign_id="campaign",
                run_id="run",
                stage="fetch",
                records=[{"url": "https://example.com"}],
                metadata={"record_count": 2},
            )


if __name__ == "__main__":
    unittest.main()
