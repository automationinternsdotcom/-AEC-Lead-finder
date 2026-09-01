"""Reproducible daily promotion scorecards and the three-day gate."""
from __future__ import annotations

import hashlib
import csv
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .ids import canonicalize_url


SCORER_VERSION = "aether-promotion-v1"
JUDGE_MODEL = "grok-4.3"
JUDGE_TEMPERATURE = 0
JUDGED_LIMITS = {
    "v2_addition_quality": 5,
    "contact_identity_alignment": 10,
    "identity_grouping_quality": 10,
}


@dataclass(slots=True)
class PromotionInputs:
    v1_qualified_ids: list[str] = field(default_factory=list)
    retained_v1_ids: list[str] = field(default_factory=list)
    quarantined_v1_ids: list[str] = field(default_factory=list)
    selected_contacts: int = 0
    reachable_contacts: int = 0
    evidence_valid_contacts: int = 0
    identity_invariants_valid: bool = False
    collision_count: int = 0
    provenance_fields_total: int = 0
    provenance_fields_valid: int = 0
    required_stages: list[str] = field(default_factory=list)
    completed_stages: list[str] = field(default_factory=list)
    resume_verified: bool = False
    artifact_integrity: bool = False
    sent_message_count: int = 0
    usage_recorded: bool = False
    duplicate_apollo_attempts: int = 0
    valid_manifests: bool = False
    cache_namespace_crossover: bool = False
    database_corruption: bool = False
    bounded_judge_evidence: dict[str, list[dict]] = field(default_factory=dict)


def collect_promotion_inputs(
    *,
    v1_leads_csv: str | Path,
    v2_leads_csv: str | Path,
    v2_uncertain_csv: str | Path,
    v2_contacts_csv: str | Path,
    comparison_manifests: Sequence[str | Path],
    sent_message_count: int,
    resume_verified: bool,
    duplicate_apollo_attempts: int = 0,
    database_corruption: bool = False,
) -> PromotionInputs:
    """Derive deterministic score inputs from frozen comparison artifacts."""
    v1_rows = _read_csv(Path(v1_leads_csv))
    v2_rows = _read_csv(Path(v2_leads_csv))
    uncertain_rows = _read_csv(Path(v2_uncertain_csv))
    contacts = _read_csv(Path(v2_contacts_csv))
    v1_ids = [_lead_identity(row) for row in v1_rows]
    v2_ids = {_lead_identity(row) for row in v2_rows}
    quarantined = {_lead_identity(row) for row in uncertain_rows}
    retained = [identifier for identifier in v1_ids if identifier in v2_ids]
    quarantined_v1 = [identifier for identifier in v1_ids if identifier in quarantined]
    selected = [row for row in contacts if str(row.get("person") or "").strip()]
    reachable = [
        row
        for row in selected
        if any(str(row.get(field) or "").strip() for field in ("email", "phone", "linkedin"))
    ]
    evidence_valid = [
        row
        for row in selected
        if str(row.get("sources") or "").strip() and _valid_json_if_present(row.get("provenance_json"))
    ]
    lead_required = ("lead_event_id", "organization_id", "primary_candidate_id", "run_id", "provenance_json")
    contact_required = ("lead_event_id", "organization_id", "person_id", "run_id", "sources")
    total_fields = len(v2_rows) * len(lead_required) + len(selected) * len(contact_required)
    valid_fields = sum(bool(str(row.get(field) or "").strip()) for row in v2_rows for field in lead_required)
    valid_fields += sum(bool(str(row.get(field) or "").strip()) for row in selected for field in contact_required)
    manifests = [_load_manifest(Path(path)) for path in comparison_manifests]
    valid_manifests = len(manifests) == 2 and all(
        item.get("status") == "completed" and _manifest_artifacts_valid(item) for item in manifests
    )
    cache_namespaces = [str(item.get("cache_namespace") or "") for item in manifests]
    crossover = len(set(cache_namespaces)) != len(cache_namespaces) or not all(cache_namespaces)
    native = _native_manifest(manifests)
    stages = native.get("stages") if isinstance(native.get("stages"), dict) else {}
    required_stages = list(stages)
    completed_stages = [
        name for name, value in stages.items() if isinstance(value, dict) and value.get("status") == "completed"
    ]
    identity_ok, collisions = _identity_invariants(v2_rows, selected)
    additions = [row for row in v2_rows if _lead_identity(row) not in set(v1_ids)]
    return PromotionInputs(
        v1_qualified_ids=v1_ids,
        retained_v1_ids=retained,
        quarantined_v1_ids=quarantined_v1,
        selected_contacts=len(selected),
        reachable_contacts=len(reachable),
        evidence_valid_contacts=len(evidence_valid),
        identity_invariants_valid=identity_ok,
        collision_count=collisions,
        provenance_fields_total=total_fields,
        provenance_fields_valid=valid_fields,
        required_stages=required_stages,
        completed_stages=completed_stages,
        resume_verified=resume_verified,
        artifact_integrity=valid_manifests,
        sent_message_count=sent_message_count,
        usage_recorded=isinstance(native.get("usage"), dict),
        duplicate_apollo_attempts=duplicate_apollo_attempts,
        valid_manifests=valid_manifests,
        cache_namespace_crossover=crossover,
        database_corruption=database_corruption,
        bounded_judge_evidence={
            "v2_addition_quality": [_sample_lead(row) for row in additions[:10]],
            "contact_identity_alignment": [_sample_contact(row) for row in selected[:10]],
            "identity_grouping_quality": [
                _sample_lead(row) for row in v2_rows[:5]
            ] + [_sample_contact(row) for row in selected[:5]],
        },
    )


JudgeCall = Callable[[str, str, float], str]


def build_scorecard(
    inputs: PromotionInputs,
    *,
    final_dir: str | Path,
    input_artifacts: Sequence[str | Path],
    judge_call: JudgeCall,
    human_override: dict | None = None,
) -> dict:
    """Write exact judge inputs/raw output and a deterministic scorecard."""
    target_dir = Path(final_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if not input_artifacts:
        raise ValueError("promotion scorecard requires input artifact hashes")
    input_hashes = {str(Path(path)): _sha256(Path(path)) for path in input_artifacts}
    prompt, allowed_evidence = _judge_prompt(inputs.bounded_judge_evidence)
    prompt_path = target_dir / "promotion_judge_prompt.json"
    _atomic_json(prompt_path, json.loads(prompt))
    raw_path = target_dir / "promotion_judge_raw.json"
    raw_response = ""
    judge_error = ""
    judged: dict[str, dict] = {}
    try:
        response = judge_call(prompt, JUDGE_MODEL, JUDGE_TEMPERATURE)
        if not isinstance(response, str):
            raise ValueError("judge response must be text")
        raw_response = response
        _atomic_text(raw_path, raw_response + ("" if raw_response.endswith("\n") else "\n"))
        judged = _validate_judge(raw_response, allowed_evidence)
    except Exception as exc:
        judge_error = f"{type(exc).__name__}: {exc}"
        if not raw_path.exists():
            _atomic_text(raw_path, raw_response)

    deterministic = _deterministic_criteria(inputs)
    criteria = [*deterministic]
    for criterion_id, maximum in JUDGED_LIMITS.items():
        item = judged.get(criterion_id)
        criteria.append(
            {
                "criterion_id": criterion_id,
                "kind": "judged",
                "maximum": maximum,
                "points": item["points"] if item else None,
                "evidence": item["evidence"] if item else [],
                "reason": item["reason"] if item else "judge output invalid or incomplete",
            }
        )
    blockers = _hard_blockers(inputs)
    override = _validate_override(human_override)
    if override and override["decision"] == "veto":
        blockers.append("human_veto")
    manual_review = bool(judge_error) or len(judged) != len(JUDGED_LIMITS)
    total = sum(item["points"] for item in criteria if isinstance(item["points"], int))
    silent_loss = "silent_v1_lead_loss" in blockers
    green = (
        not manual_review
        and total >= 85
        and inputs.valid_manifests
        and not silent_loss
        and not blockers
    )
    scorecard = {
        "scorer_version": SCORER_VERSION,
        "judge": {
            "model": JUDGE_MODEL,
            "temperature": JUDGE_TEMPERATURE,
            "prompt_hash": _hash_bytes(prompt.encode()),
            "prompt_path": str(prompt_path),
            "raw_response_path": str(raw_path),
            "valid": not manual_review,
            "error": judge_error,
        },
        "input_artifact_hashes": input_hashes,
        "deterministic_inputs": asdict(inputs) | {"bounded_judge_evidence": "stored in prompt"},
        "criteria": criteria,
        "total_points": total,
        "hard_blockers": blockers,
        "manual_review_required": manual_review,
        "human_override": override,
        "decision": "green" if green else "not_green",
    }
    scorecard_path = target_dir / "promotion_scorecard.json"
    _atomic_json(scorecard_path, scorecard)
    return scorecard


def aggregate_promotion(scorecards: Sequence[dict]) -> dict:
    if len(scorecards) != 3:
        raise ValueError("promotion requires exactly three daily scorecards")
    invalid = [
        index
        for index, card in enumerate(scorecards, start=1)
        if card.get("manual_review_required")
        or card.get("hard_blockers")
        or (card.get("human_override") or {}).get("decision") == "veto"
    ]
    green_days = sum(card.get("decision") == "green" for card in scorecards)
    promote = green_days >= 2 and not invalid
    return {
        "scorer_version": SCORER_VERSION,
        "green_days": green_days,
        "invalid_or_blocked_days": invalid,
        "decision": "promote_v2" if promote else "retain_v1",
    }


def _deterministic_criteria(inputs: PromotionInputs) -> list[dict]:
    expected = set(inputs.v1_qualified_ids)
    accounted = set(inputs.retained_v1_ids) | set(inputs.quarantined_v1_ids)
    retention_ratio = len(expected & accounted) / len(expected) if expected else 1.0
    contact_denominator = max(inputs.selected_contacts, 1)
    reachable = min(inputs.reachable_contacts / contact_denominator, 1.0)
    sourced = min(inputs.evidence_valid_contacts / contact_denominator, 1.0)
    provenance = (
        min(inputs.provenance_fields_valid / inputs.provenance_fields_total, 1.0)
        if inputs.provenance_fields_total
        else 0.0
    )
    required = set(inputs.required_stages)
    stage_ratio = len(required & set(inputs.completed_stages)) / len(required) if required else 0.0
    pipeline_points = round(4 * stage_ratio) + (3 if inputs.resume_verified else 0) + (
        3 if inputs.artifact_integrity else 0
    )
    operations_points = (
        (4 if inputs.sent_message_count == 1 else 0)
        + (3 if inputs.usage_recorded else 0)
        + (3 if inputs.duplicate_apollo_attempts == 0 else 0)
    )
    return [
        _criterion("v1_accounting", 20, round(20 * retention_ratio), {
            "expected": sorted(expected), "accounted": sorted(accounted)
        }),
        _criterion("contact_contract_quality", 15, round(15 * (reachable + sourced) / 2), {
            "selected": inputs.selected_contacts,
            "reachable": inputs.reachable_contacts,
            "evidence_valid": inputs.evidence_valid_contacts,
        }),
        _criterion("identity_invariants", 5, 5 if inputs.identity_invariants_valid and inputs.collision_count == 0 else 0, {
            "invariants_valid": inputs.identity_invariants_valid,
            "collision_count": inputs.collision_count,
        }),
        _criterion("provenance_contracts", 15, round(15 * provenance), {
            "valid": inputs.provenance_fields_valid, "total": inputs.provenance_fields_total
        }),
        _criterion("pipeline_integrity", 10, pipeline_points, {
            "required_stages": sorted(required),
            "completed_stages": sorted(set(inputs.completed_stages)),
            "resume_verified": inputs.resume_verified,
            "artifact_integrity": inputs.artifact_integrity,
        }),
        _criterion("delivery_usage_apollo", 10, operations_points, {
            "sent_message_count": inputs.sent_message_count,
            "usage_recorded": inputs.usage_recorded,
            "duplicate_apollo_attempts": inputs.duplicate_apollo_attempts,
        }),
    ]


def _criterion(identifier: str, maximum: int, points: int, evidence: dict) -> dict:
    return {
        "criterion_id": identifier,
        "kind": "deterministic",
        "maximum": maximum,
        "points": max(0, min(points, maximum)),
        "evidence": evidence,
    }


def _hard_blockers(inputs: PromotionInputs) -> list[str]:
    blockers = []
    if inputs.database_corruption:
        blockers.append("database_corruption")
    if inputs.sent_message_count > 1:
        blockers.append("duplicate_gmail_send")
    if inputs.duplicate_apollo_attempts:
        blockers.append("duplicate_apollo_charge")
    if inputs.cache_namespace_crossover:
        blockers.append("cache_namespace_crossover")
    if set(inputs.v1_qualified_ids) - (
        set(inputs.retained_v1_ids) | set(inputs.quarantined_v1_ids)
    ):
        blockers.append("silent_v1_lead_loss")
    if not inputs.valid_manifests:
        blockers.append("invalid_manifest")
    return blockers


def _judge_prompt(evidence: dict[str, list[dict]]) -> tuple[str, dict[str, set[str]]]:
    bounded = {}
    allowed = {}
    for criterion in JUDGED_LIMITS:
        rows = list(evidence.get(criterion, []))[:10]
        if not rows:
            rows = [{"no_samples_available": True}]
        bounded[criterion] = [
            {"evidence_ref": f"{criterion}:{index}", "payload": row}
            for index, row in enumerate(rows, start=1)
        ]
        allowed[criterion] = {row["evidence_ref"] for row in bounded[criterion]}
    prompt = json.dumps(
        {
            "rubric_version": SCORER_VERSION,
            "instruction": "Return only JSON with criteria. Use only supplied evidence.",
            "criteria_limits": JUDGED_LIMITS,
            "evidence": bounded,
            "response_schema": {
                "criteria": [
                    {
                        "criterion_id": "one exact criterion ID",
                        "points": "integer within criterion limit",
                        "evidence": ["one or more supplied evidence references"],
                        "reason": "evidence-linked reason",
                    }
                ]
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return prompt, allowed


def _validate_judge(raw: str, allowed_evidence: dict[str, set[str]]) -> dict[str, dict]:
    payload = json.loads(raw)
    rows = payload.get("criteria")
    if not isinstance(rows, list) or len(rows) != len(JUDGED_LIMITS):
        raise ValueError("judge must return every criterion exactly once")
    output = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("judge criterion must be an object")
        identifier = row.get("criterion_id")
        if identifier not in JUDGED_LIMITS or identifier in output:
            raise ValueError("judge criterion IDs must be exact and unique")
        points = row.get("points")
        if isinstance(points, bool) or not isinstance(points, int):
            raise ValueError("judge points must be integers")
        if not 0 <= points <= JUDGED_LIMITS[identifier]:
            raise ValueError("judge points exceed criterion bounds")
        evidence = row.get("evidence")
        reason = row.get("reason")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(x, str) for x in evidence):
            raise ValueError("judge criterion needs evidence references")
        if not set(evidence) <= allowed_evidence[identifier]:
            raise ValueError("judge cited evidence outside the bounded input")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("judge criterion needs a reason")
        output[identifier] = {"points": points, "evidence": evidence, "reason": reason}
    return output


def _validate_override(value: dict | None) -> dict | None:
    if value is None:
        return None
    if value.get("decision") not in {"veto", "approve"}:
        raise ValueError("human override decision must be veto or approve")
    if not str(value.get("reason") or "").strip() or not str(value.get("signed_by") or "").strip():
        raise ValueError("human overrides require a signed reason")
    return {
        "decision": value["decision"],
        "reason": str(value["reason"]),
        "signed_by": str(value["signed_by"]),
    }


def _sha256(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    temp = path.with_name(path.name + ".tmp")
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    with temp.open("w", encoding="utf-8") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


def _atomic_text(path: Path, value: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        file.write(value)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def _lead_identity(row: dict[str, str]) -> str:
    link = str(row.get("link") or "").strip()
    if link:
        try:
            return canonicalize_url(link)
        except ValueError:
            pass
    return str(row.get("lead_event_id") or "").strip()


def _valid_json_if_present(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return True
    try:
        json.loads(raw)
        return True
    except json.JSONDecodeError:
        return False


def _load_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be an object: {path}")
    payload["_path"] = str(path)
    return payload


def _manifest_artifacts_valid(manifest: dict) -> bool:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    for item in artifacts:
        if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
            return False
        path = Path(str(item["path"]))
        if not path.is_file() or _sha256(path) != item["sha256"]:
            return False
    return True


def _native_manifest(manifests: Sequence[dict]) -> dict:
    v2 = next((item for item in manifests if item.get("version") == "V2"), {})
    paths = v2.get("native_manifests") if isinstance(v2, dict) else []
    if not isinstance(paths, list) or not paths:
        return {}
    try:
        payload = json.loads(Path(paths[-1]).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _identity_invariants(leads: Sequence[dict], contacts: Sequence[dict]) -> tuple[bool, int]:
    collisions = 0
    seen_events: dict[str, tuple[str, str]] = {}
    for row in leads:
        identifier = str(row.get("lead_event_id") or "")
        identity = (str(row.get("organization_id") or ""), _lead_identity(row))
        if not identifier or not all(identity):
            collisions += 1
        elif identifier in seen_events and seen_events[identifier] != identity:
            collisions += 1
        seen_events[identifier] = identity
    seen_people: dict[str, tuple[str, str]] = {}
    for row in contacts:
        identifier = str(row.get("person_id") or "")
        identity = (
            str(row.get("organization_id") or ""),
            normalize_person(str(row.get("person") or "")),
        )
        if not identifier or not all(identity):
            collisions += 1
        elif identifier in seen_people and seen_people[identifier] != identity:
            collisions += 1
        seen_people[identifier] = identity
    return collisions == 0, collisions


def normalize_person(value: str) -> str:
    return " ".join(value.casefold().split())


def _sample_lead(row: dict) -> dict:
    return {
        key: row.get(key, "")
        for key in (
            "lead_event_id",
            "organization_id",
            "business_name",
            "event",
            "location",
            "link",
            "supporting_candidate_ids",
            "provenance_json",
        )
    }


def _sample_contact(row: dict) -> dict:
    return {
        key: row.get(key, "")
        for key in (
            "lead_event_id",
            "organization_id",
            "person_id",
            "person",
            "title",
            "email",
            "phone",
            "linkedin",
            "sources",
            "verification_status",
        )
    }
