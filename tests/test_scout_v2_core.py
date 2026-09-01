"""Core identity, contract, state, and artifact guarantees for Scout V2."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.artifacts import ArtifactStore, new_manifest  # noqa: E402
from v2.contracts import (  # noqa: E402
    DiscoveryCandidate,
    RecordStatus,
    ReviewItem,
    StageStatus,
)
from v2.ids import candidate_id, canonicalize_url, stable_uuid  # noqa: E402
from v2.qualification import JudgmentPayload  # noqa: E402
from v2.state import StateStore  # noqa: E402


def test_canonical_url_and_ids_are_stable():
    assert canonicalize_url("HTTPS://Example.COM:443/a//b/?utm_source=x&z=2&a=1#part") == (
        "https://example.com/a/b?a=1&z=2"
    )
    assert stable_uuid("organization", " ACME, Inc. ", "Phoenix") == stable_uuid(
        "organization", "acme inc", "phoenix"
    )
    assert candidate_id("curated", "", "https://example.com/a?utm_campaign=x") == candidate_id(
        "curated", "", "https://example.com/a"
    )


def test_review_candidate_requires_validation_reason():
    payload = {
        "candidate_id": "candidate-1",
        "run_id": "run-1",
        "provider": "curated",
        "discovered_url": "https://example.com/a",
        "resolved_url": "https://example.com/a",
        "canonical_url": "https://example.com/a",
        "source_id": "source-1",
        "source_name": "Example",
        "source_domain": "example.com",
        "record_status": RecordStatus.REVIEW,
    }
    with pytest.raises(ValidationError, match="validation_errors"):
        DiscoveryCandidate.model_validate(payload)
    payload["validation_errors"] = ["publication date missing"]
    candidate = DiscoveryCandidate.model_validate(payload)
    assert candidate.record_status == RecordStatus.REVIEW


@pytest.mark.parametrize(
    "value",
    ["Aug 30, 2026", "August 30, 2026", "08/30/2026", "2026-08-30"],
)
def test_qualification_accepts_common_provider_date_formats(value):
    payload = JudgmentPayload(
        qualified=False,
        filter_reason="not a qualifying commercial-property event",
        date_posted=value,
    )
    assert payload.date_posted.isoformat() == "2026-08-30"


def test_state_migration_is_idempotent_and_tracks_resume(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    assert store.migrate() == 5
    assert store.migrate() == 5
    store.create_run("run-1", "2026-08-28", "2026-08-27", {"workers": 5})
    store.set_stage_status("run-1", "discover", StageStatus.RUNNING)
    store.set_stage_status("run-1", "discover", StageStatus.COMPLETED, counters={"candidates": 3})
    store.set_stage_status("run-1", "qualify", StageStatus.RUNNING)
    store.set_stage_status("run-1", "qualify", StageStatus.FAILED, error={"code": "boom"})
    store.set_stage_status("run-1", "qualify", StageStatus.RUNNING)

    assert store.completed_stages("run-1") == {"discover"}
    with sqlite3.connect(tmp_path / "scout.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM v2_schema_migrations").fetchone()[0] == 5
        assert conn.execute(
            "SELECT attempt_count FROM v2_stage_runs WHERE run_id='run-1' AND stage='qualify'"
        ).fetchone()[0] == 2


def test_only_rejected_qualifications_are_reused_across_runs(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-a", "2026-01-14", "2026-01-01")
    store.create_run("run-b", "2026-01-31", "2026-01-15")
    store.upsert_source(
        "source-1", "Example", "https://example.com/", "example.com"
    )
    store.save_candidate(
        DiscoveryCandidate(
            candidate_id="candidate-1",
            run_id="run-a",
            provider="curated",
            discovered_url="https://example.com/article",
            resolved_url="https://example.com/article",
            canonical_url="https://example.com/article",
            source_id="source-1",
            source_name="Example",
            source_domain="example.com",
        )
    )
    store.record_provider_attempt(
        attempt_id="attempt-1",
        run_id="run-a",
        stage="qualify",
        provider="model",
        target_type="discovery_candidate",
        target_id="candidate-1",
        status="completed",
    )

    # A legacy completed provider attempt has no outcome record and cannot be
    # reused safely: it may have created a run-scoped qualified LeadEvent.
    assert not store.completed_qualification_candidate_ids(
        since_date="2026-01-01", stamp="2026-01-14"
    )
    assert not store.completed_qualification_candidate_ids(
        since_date="2026-01-15", stamp="2026-01-31"
    )
    store.record_qualification_completion(
        candidate_id="candidate-1",
        since_date="2026-01-15",
        stamp="2026-01-31",
        run_id="run-b",
        outcome="rejected",
    )
    assert store.completed_qualification_candidate_ids(
        since_date="2026-01-15", stamp="2026-01-31"
    ) == {"candidate-1"}
    store.record_qualification_completion(
        candidate_id="candidate-1",
        since_date="2026-01-01",
        stamp="2026-01-14",
        run_id="run-a",
        outcome="qualified",
    )
    assert not store.completed_qualification_candidate_ids(
        since_date="2026-01-01", stamp="2026-01-14"
    )
    with pytest.raises(ValueError, match="supplied together"):
        store.completed_qualification_candidate_ids(since_date="2026-01-01")


def test_review_lane_returns_only_retryable_items(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    retryable = ReviewItem(
        review_id="review-1",
        run_id="run-1",
        stage="discover",
        record_type="candidate",
        record_id="candidate-1",
        reason_code="undated",
        validation_errors=["publication date missing"],
        retry_count=1,
    )
    exhausted = retryable.model_copy(
        update={"review_id": "review-2", "record_id": "candidate-2", "retry_count": 2}
    )
    store.add_review(retryable)
    store.add_review(exhausted)

    assert [item.review_id for item in store.eligible_reviews("discover", max_retries=2)] == [
        "review-1"
    ]


def test_artifacts_are_atomic_hashed_and_manifested(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    artifacts = ArtifactStore(tmp_path / "results", "2026-08-28", "run-1", store)
    raw = artifacts.write_raw("discover", "page.json", {"url": "https://example.com"})
    final = artifacts.write_jsonl("discover", "candidates.jsonl", [{"candidate_id": "c1"}])
    manifest = new_manifest("run-1", "2026-08-28", "2026-08-27", {"workers": 5})
    manifest.status = StageStatus.COMPLETED
    manifest.artifacts.extend([raw, final])
    artifacts.write_manifest(manifest)

    assert not list(artifacts.run_dir.rglob("*.tmp"))
    assert len(raw["sha256"]) == 64
    assert json.loads(artifacts.manifest_path.read_text())["status"] == "completed"
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM v2_artifacts").fetchone()[0] == 2
        assert conn.execute("SELECT status FROM v2_runs WHERE run_id='run-1'").fetchone()[0] == (
            "completed"
        )
