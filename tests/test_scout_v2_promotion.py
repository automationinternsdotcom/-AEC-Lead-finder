"""Promotion scorecards are reproducible, strict, and blocker-aware."""
from __future__ import annotations

import json
import csv
import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.promotion import (  # noqa: E402
    PromotionInputs,
    aggregate_promotion,
    build_scorecard,
    collect_promotion_inputs,
)


def perfect_inputs():
    return PromotionInputs(
        v1_qualified_ids=["a", "b"],
        retained_v1_ids=["a", "b"],
        selected_contacts=2,
        reachable_contacts=2,
        evidence_valid_contacts=2,
        identity_invariants_valid=True,
        provenance_fields_total=10,
        provenance_fields_valid=10,
        required_stages=["discover", "export"],
        completed_stages=["discover", "export"],
        resume_verified=True,
        artifact_integrity=True,
        sent_message_count=1,
        usage_recorded=True,
        valid_manifests=True,
        bounded_judge_evidence={key: [{"ref": key}] for key in (
            "v2_addition_quality", "contact_identity_alignment", "identity_grouping_quality"
        )},
    )


def valid_judge(prompt, model, temperature):
    assert model == "grok-4.3" and temperature == 0
    assert len(json.loads(prompt)["evidence"]["v2_addition_quality"]) <= 10
    return json.dumps({"criteria": [
        {"criterion_id": "v2_addition_quality", "points": 5, "evidence": ["v2_addition_quality:1"], "reason": "supported"},
        {"criterion_id": "contact_identity_alignment", "points": 10, "evidence": ["contact_identity_alignment:1"], "reason": "aligned"},
        {"criterion_id": "identity_grouping_quality", "points": 10, "evidence": ["identity_grouping_quality:1"], "reason": "consistent"},
    ]})


def test_green_scorecard_records_audit_inputs_and_aggregates(tmp_path):
    artifact = tmp_path / "manifest.json"
    artifact.write_text('{"status":"completed"}\n')
    card = build_scorecard(
        perfect_inputs(), final_dir=tmp_path / "final", input_artifacts=[artifact], judge_call=valid_judge
    )
    assert card["total_points"] == 100 and card["decision"] == "green"
    assert card["judge"]["prompt_hash"]
    assert Path(card["judge"]["raw_response_path"]).exists()
    assert aggregate_promotion([card, card, card])["decision"] == "promote_v2"


def test_malformed_judge_routes_to_manual_review_not_zero_quality(tmp_path):
    artifact = tmp_path / "manifest.json"
    artifact.write_text("{}")
    card = build_scorecard(
        perfect_inputs(),
        final_dir=tmp_path / "final",
        input_artifacts=[artifact],
        judge_call=lambda *args: '{"criteria":[]}',
    )
    judged = [item for item in card["criteria"] if item["kind"] == "judged"]
    assert card["manual_review_required"] and card["decision"] == "not_green"
    assert all(item["points"] is None for item in judged)


def test_silent_loss_and_signed_veto_block_promotion(tmp_path):
    inputs = perfect_inputs()
    inputs.retained_v1_ids = ["a"]
    artifact = tmp_path / "manifest.json"
    artifact.write_text("{}")
    card = build_scorecard(
        inputs,
        final_dir=tmp_path / "final",
        input_artifacts=[artifact],
        judge_call=valid_judge,
        human_override={"decision": "veto", "reason": "sample mismatch", "signed_by": "reviewer"},
    )
    assert "silent_v1_lead_loss" in card["hard_blockers"]
    assert "human_veto" in card["hard_blockers"]
    assert aggregate_promotion([card, card, card])["decision"] == "retain_v1"


def test_collector_accounts_for_v1_by_canonical_source_and_validates_manifests(tmp_path):
    def write_csv(name, fields, rows):
        path = tmp_path / name
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    v1 = write_csv("v1.csv", ["link"], [{"link": "https://example.com/lead?utm_source=x"}])
    v2 = write_csv(
        "v2.csv",
        ["link", "lead_event_id", "organization_id", "primary_candidate_id", "run_id", "provenance_json", "business_name", "event", "location", "supporting_candidate_ids"],
        [{"link": "https://example.com/lead", "lead_event_id": "event-1", "organization_id": "org-1", "primary_candidate_id": "candidate-1", "run_id": "run-1", "provenance_json": "{}", "business_name": "Acme", "event": "Opened", "location": "Phoenix", "supporting_candidate_ids": "candidate-1"}],
    )
    uncertain = write_csv("uncertain.csv", ["link"], [])
    contacts = write_csv(
        "contacts.csv",
        ["person", "email", "phone", "linkedin", "sources", "provenance_json", "lead_event_id", "organization_id", "person_id", "run_id", "title", "verification_status"],
        [{"person": "Jane", "email": "jane@example.com", "phone": "", "linkedin": "", "sources": "https://example.com/team", "provenance_json": "[]", "lead_event_id": "event-1", "organization_id": "org-1", "person_id": "person-1", "run_id": "run-1", "title": "GM", "verification_status": "verified"}],
    )
    native = tmp_path / "native.json"
    native.write_text(json.dumps({"stages": {"export": {"status": "completed"}}, "usage": {}}))
    manifests = []
    for version, namespace in (("V1", "scout-db-v1-sha-comparison-1"), ("V2", "scout-db-v2-3-comparison-1")):
        path = tmp_path / f"{version}.manifest.json"
        artifact = v1 if version == "V1" else v2
        path.write_text(json.dumps({
            "version": version,
            "status": "completed",
            "cache_namespace": namespace,
            "native_manifests": [str(native)] if version == "V2" else [],
            "artifacts": [{"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}],
        }))
        manifests.append(path)
    inputs = collect_promotion_inputs(
        v1_leads_csv=v1,
        v2_leads_csv=v2,
        v2_uncertain_csv=uncertain,
        v2_contacts_csv=contacts,
        comparison_manifests=manifests,
        sent_message_count=1,
        resume_verified=True,
    )
    assert inputs.retained_v1_ids == ["https://example.com/lead"]
    assert inputs.valid_manifests and inputs.identity_invariants_valid
    assert inputs.completed_stages == ["export"]
