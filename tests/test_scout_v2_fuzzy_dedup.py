"""Fuzzy event grouping is complete, auditable, and lossless."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.artifacts import ArtifactStore  # noqa: E402
from v2.contracts import DiscoveryCandidate, Evidence, LeadEvent, Organization  # noqa: E402
from v2.dedup import FuzzyEventDeduper, dedupe_candidates_exact  # noqa: E402
from v2.state import StateStore  # noqa: E402


def setup(tmp_path):
    store = StateStore(tmp_path / "scout.db")
    store.migrate()
    store.create_run("run-1", "2026-08-28", "2026-08-27")
    store.upsert_source("source-1", "Example", "https://example.com/", "example.com")
    evidence = []
    events = []
    for index, org_name in ((1, "Acme Center"), (2, "Acme Centre LLC")):
        candidate = DiscoveryCandidate(
            candidate_id=f"candidate-{index}",
            run_id="run-1",
            provider="curated",
            discovered_url=f"https://example.com/{index}",
            resolved_url=f"https://example.com/{index}",
            canonical_url=f"https://example.com/{index}",
            title="Acme opens",
            source_id="source-1",
            source_name="Example",
            source_domain="example.com",
            published_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
        store.save_candidate(candidate)
        item_evidence = [Evidence(url=candidate.canonical_url, supports="Source")]
        evidence.extend(item_evidence)
        organization = Organization(
            organization_id=f"org-{index}",
            canonical_name=org_name,
            location="Phoenix, Arizona",
            evidence=item_evidence,
        )
        store.save_organization(organization)
        event = LeadEvent(
            lead_event_id=f"event-{index}",
            run_id="run-1",
            organization_id=organization.organization_id,
            primary_candidate_id=candidate.candidate_id,
            supporting_candidate_ids=[candidate.candidate_id],
            event="Opened the same retail center.",
            location="Phoenix, Arizona",
            date_posted=date(2026, 8, 28),
            priority="high",
            evidence=item_evidence,
        )
        store.save_lead_event(event)
        events.append(event)
    artifacts = ArtifactStore(tmp_path / "results", "2026-08-28", "run-1", store)
    return store, artifacts, events


def test_exact_candidate_dedup_preserves_support_ids():
    base = {
        "run_id": "run-1",
        "provider": "curated",
        "discovered_url": "https://example.com/a",
        "resolved_url": "https://example.com/a",
        "canonical_url": "https://example.com/a",
        "title": "Story",
        "source_id": "source-1",
        "source_name": "Example",
        "source_domain": "example.com",
    }
    first = DiscoveryCandidate(candidate_id="c1", **base)
    second = DiscoveryCandidate(candidate_id="c2", **(base | {"provider": "rss"}))
    kept = dedupe_candidates_exact([first, second])
    assert len(kept) == 1
    assert set(kept[0].metadata["exact_duplicate_candidate_ids"]) == {"c1", "c2"}


def test_valid_fuzzy_merge_retains_sources_without_conflating_organizations(tmp_path):
    store, artifacts, events = setup(tmp_path)
    response = [{"kept_id": "event-1", "member_ids": ["event-1", "event-2"]}]
    service = FuzzyEventDeduper(
        store,
        artifacts,
        "grok-mini",
        lambda model, prompt, tools: (json.dumps(response), {}),
    )
    merged, reviews = service.dedupe(events)

    assert not reviews and len(merged) == 1
    assert merged[0].supporting_candidate_ids == ["candidate-1", "candidate-2"]
    assert [event.lead_event_id for event in store.active_events_for_run("run-1")] == [
        "event-1"
    ]
    assert store.organizations({"org-1"})[0].aliases == []
    assert store.organizations({"org-2"})[0].canonical_name == "Acme Centre LLC"


def test_invalid_fuzzy_merge_keeps_every_event_and_opens_review(tmp_path):
    store, artifacts, events = setup(tmp_path)
    service = FuzzyEventDeduper(
        store,
        artifacts,
        "grok-mini",
        lambda model, prompt, tools: (
            json.dumps([{"kept_id": "event-1", "member_ids": ["event-1"]}]),
            {},
        ),
    )
    kept, reviews = service.dedupe(events, attempts=2)

    assert len(kept) == 2 and len(reviews) == 2
    assert len(store.active_events_for_run("run-1")) == 2
