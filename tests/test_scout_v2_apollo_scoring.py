"""Apollo authorization/cache and completeness-checked score batches."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.apollo import (  # noqa: E402
    ApolloFatalError,
    ApolloResolver,
    ApolloTransientError,
)
from v2.artifacts import ArtifactStore  # noqa: E402
from v2.contracts import DiscoveryCandidate, Evidence, LeadEvent, Organization  # noqa: E402
from v2.scoring import ScoringService  # noqa: E402
from v2.state import StateStore  # noqa: E402


def setup(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    store.upsert_source("source-1", "Example", "https://example.com/", "example.com")
    candidate = DiscoveryCandidate(
        candidate_id="candidate-1",
        run_id="run-1",
        provider="curated",
        discovered_url="https://example.com/a",
        resolved_url="https://example.com/a",
        canonical_url="https://example.com/a",
        title="Opening",
        source_id="source-1",
        source_name="Example",
        source_domain="example.com",
        published_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    store.save_candidate(candidate)
    evidence = [Evidence(url="https://example.com/a", supports="Source")]
    organization = Organization(
        organization_id="org-1",
        canonical_name="Acme",
        location="Phoenix, Arizona",
        evidence=evidence,
    )
    store.save_organization(organization)
    event = LeadEvent(
        lead_event_id="event-1",
        run_id="run-1",
        organization_id="org-1",
        primary_candidate_id="candidate-1",
        supporting_candidate_ids=["candidate-1"],
        event="Acme opened a new property.",
        location="Phoenix, Arizona",
        date_posted=date(2026, 8, 28),
        priority="high",
        evidence=evidence,
    )
    store.save_lead_event(event)
    artifacts = ArtifactStore(tmp_path / "results", "2026-08-28", "run-1", store)
    return store, artifacts, event


def test_apollo_dry_run_never_calls_and_null_is_cached(tmp_path):
    store, _, _ = setup(tmp_path)
    calls = []
    resolver = ApolloResolver(
        store,
        api_key="key",
        request_match=lambda key, body: calls.append(body) or {"person": None},
    )
    assert resolver.resolve("Jane", "Acme", spend=False).status == "dry_run"
    first = resolver.resolve("Jane", "Acme", spend=True)
    second = resolver.resolve(" jane ", "ACME", spend=True)

    assert first.status == second.status == "null"
    assert not first.cached and second.cached
    assert first.billable and second.billable
    assert len(calls) == 1


def test_apollo_phone_reveal_is_separately_authorized(tmp_path):
    store, _, _ = setup(tmp_path)
    resolver = ApolloResolver(store, api_key="key", request_match=lambda key, body: {})
    with pytest.raises(ApolloFatalError, match="webhook"):
        resolver.resolve("Jane", "Acme", spend=True, reveal_phone=True)


def test_apollo_transient_failure_is_not_cached(tmp_path):
    store, _, _ = setup(tmp_path)
    calls = []

    def request(key, body):
        calls.append(body)
        raise ApolloTransientError("temporary")

    resolver = ApolloResolver(store, api_key="key", request_match=request)
    for _ in range(2):
        with pytest.raises(ApolloTransientError):
            resolver.resolve("Jane", "Acme", spend=True)
    assert len(calls) == 2


def test_scoring_retries_incomplete_batch_and_preserves_zero(tmp_path):
    store, artifacts, event = setup(tmp_path)
    responses = iter([json.dumps({}), json.dumps({"event-1": 0})])
    service = ScoringService(
        store,
        artifacts,
        "grok-4.3",
        call_model=lambda model, prompt, tools: (next(responses), {"total_tokens": 10}),
    )
    scores, reviews = service.score([event], [])

    assert not reviews
    assert scores[0].score == 0
    assert store.scores_for_run("run-1")[0].score == 0
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM v2_provider_attempts").fetchone()[0] == 2


def test_scoring_quarantines_incomplete_batch_without_deleting_event(tmp_path):
    store, artifacts, event = setup(tmp_path)
    service = ScoringService(
        store,
        artifacts,
        "grok-4.3",
        call_model=lambda model, prompt, tools: (json.dumps({"unknown": 90}), {}),
    )
    scores, reviews = service.score([event], [], attempts=2)

    assert not scores and reviews[0].reason_code == "score_batch_incomplete"
    assert store.events_for_run("run-1")[0].lead_event_id == "event-1"
