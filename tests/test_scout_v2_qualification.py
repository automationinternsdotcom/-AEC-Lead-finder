"""Strict qualification behavior: valid, explicit rejection, and quarantine."""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.artifacts import ArtifactStore  # noqa: E402
from v2.contracts import DiscoveryCandidate, RecordStatus  # noqa: E402
from v2.qualification import JudgmentPayload, QualificationService  # noqa: E402
from v2.state import StateStore  # noqa: E402


def setup(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    store.upsert_source("source-1", "Example", "https://example.com/", "example.com")
    artifacts = ArtifactStore(tmp_path / "results", "2026-08-28", "run-1", store)
    return store, artifacts


def candidate(cid="candidate-1", url="https://example.com/a"):
    return DiscoveryCandidate(
        candidate_id=cid,
        run_id="run-1",
        provider="curated",
        discovered_url=url,
        resolved_url=url,
        canonical_url=url,
        title="Phoenix commercial property opens",
        source_id="source-1",
        source_name="Example",
        source_domain="example.com",
        published_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )


def valid_payload():
    return {
        "qualified": True,
        "business_name": "Acme Marketplace",
        "person": "Jane Manager",
        "event": "Opened a new retail marketplace.",
        "date_posted": "2026-08-28",
        "location": "Phoenix, Arizona",
        "summary": "Acme opened a retail property.",
        "state": "Arizona",
        "priority": "high",
        "property_type": "retail",
        "service_angle": "Aether can serve as a strategic partner.",
        "filter_reason": "A new operating property needs facilities support.",
        "confidence": "high",
    }


def test_judgment_normalizes_grok_optional_date_and_numeric_confidence():
    payload = valid_payload()
    payload["date_posted"] = ""
    payload["confidence"] = 85

    judgment = JudgmentPayload.model_validate(payload)

    assert judgment.date_posted is None
    assert judgment.confidence == "high"


def test_qualification_uses_configured_workers(tmp_path):
    store, artifacts = setup(tmp_path)
    items = [
        candidate(f"candidate-{index}", f"https://example.com/{index}")
        for index in range(3)
    ]
    for item in items:
        store.save_candidate(item)
    barrier = threading.Barrier(3)

    def concurrent_model_call(model, prompt, tools):
        barrier.wait(timeout=2)
        return json.dumps({"qualified": False, "filter_reason": "Not a lead."}), {}

    result = QualificationService(
        store,
        artifacts,
        "grok-4.3",
        call_model=concurrent_model_call,
        workers=3,
    ).qualify(items)

    assert set(result.rejected_candidate_ids) == {
        "candidate-0",
        "candidate-1",
        "candidate-2",
    }


def test_qualification_persists_stable_entities_and_usage(tmp_path):
    store, artifacts = setup(tmp_path)
    item = candidate()
    store.save_candidate(item)
    service = QualificationService(
        store,
        artifacts,
        "grok-4.3",
        call_model=lambda model, prompt, tools: (json.dumps(valid_payload()), {"total_tokens": 123}),
    )
    result = service.qualify([item])

    assert len(result.events) == 1
    assert len(result.people) == 1
    assert store.events_for_run("run-1")[0].supporting_candidate_ids == ["candidate-1"]
    assert store.people()[0].name == "Jane Manager"
    with store.connect() as conn:
        attempt = conn.execute("SELECT status, token_usage_json FROM v2_provider_attempts").fetchone()
    assert attempt["status"] == "completed"
    assert json.loads(attempt["token_usage_json"])["total_tokens"] == 123


def test_explicit_rejection_is_not_review(tmp_path):
    store, artifacts = setup(tmp_path)
    item = candidate()
    store.save_candidate(item)
    service = QualificationService(
        store,
        artifacts,
        "grok-4.3",
        call_model=lambda model, prompt, tools: (
            json.dumps({"qualified": False, "filter_reason": "Only macro commentary."}),
            {},
        ),
    )
    result = service.qualify([item])

    assert result.rejected_candidate_ids == ["candidate-1"]
    assert not result.reviews
    assert store.candidates_for_run("run-1")[0].record_status == RecordStatus.REJECTED


def test_incomplete_model_response_enters_review_without_disappearing(tmp_path):
    store, artifacts = setup(tmp_path)
    item = candidate()
    store.save_candidate(item)
    service = QualificationService(
        store,
        artifacts,
        "grok-4.3",
        call_model=lambda model, prompt, tools: (
            json.dumps({"qualified": True, "business_name": "Acme"}),
            {},
        ),
    )
    result = service.qualify([item])

    assert len(result.reviews) == 1
    assert not result.events and not result.rejected_candidate_ids
    stored = store.candidates_for_run("run-1")[0]
    assert stored.record_status == RecordStatus.REVIEW
    assert "qualified result missing fields" in stored.validation_errors[-1]


def test_duplicate_event_preserves_all_candidate_sources(tmp_path):
    store, artifacts = setup(tmp_path)
    first = candidate("candidate-1", "https://example.com/a")
    second = candidate("candidate-2", "https://example.com/b")
    store.save_candidate(first)
    store.save_candidate(second)
    service = QualificationService(
        store,
        artifacts,
        "grok-4.3",
        call_model=lambda model, prompt, tools: (json.dumps(valid_payload()), {}),
    )
    service.qualify([first, second])

    events = store.events_for_run("run-1")
    assert len(events) == 1
    assert events[0].supporting_candidate_ids == ["candidate-1", "candidate-2"]
