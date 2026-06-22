"""Run directory and checkpoint helpers for the Phase 2 engine.

This is deterministic bookkeeping only. Codex still performs reasoning and
browser/chat work; these helpers make the run auditable and resumable.
"""
from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from pipeline.config import ROOT
from pipeline.contracts import (
    NextAction,
    RunManifest,
    StageCheckpoint,
    StageName,
    dump_json_model,
    load_manifest,
    utc_now,
)
from pipeline.spec import CampaignSpecV2, load_campaign_spec_v2, resolve_spec_path

RUNS_DIR = ROOT / "runs"
STAGE_ORDER: tuple[StageName, ...] = (
    "fetch",
    "extract",
    "qualify",
    "enrich",
    "preview",
    "deliver",
)


def new_run_id(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def init_run(
    campaign: str | Path | None = None,
    *,
    run_id: str | None = None,
    runs_dir: Path = RUNS_DIR,
) -> Path:
    """Create a run folder and write the resolved spec + manifest."""
    spec_path = resolve_spec_path(campaign)
    spec = load_campaign_spec_v2(campaign)
    rid = run_id or new_run_id()
    run_dir = runs_dir / spec.campaign_id / rid
    _create_run_dirs(run_dir)

    resolved_spec_path = run_dir / "spec.resolved.yaml"
    resolved_spec_path.write_text(
        yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    manifest = build_manifest(spec, rid, spec_path)
    dump_json_model(manifest, run_dir / "manifest.json")
    dump_json_model(manifest, run_dir / "checkpoints.json")
    return run_dir


def build_manifest(spec: CampaignSpecV2, run_id: str, spec_path: Path) -> RunManifest:
    routes = {
        "fetch": spec.routing.fetch,
        "extract": spec.routing.extract,
        "qualify": spec.routing.qualify,
        "enrich": spec.routing.enrich,
        "preview": spec.routing.preview,
        "deliver": spec.routing.deliver,
    }
    return RunManifest(
        campaign_id=spec.campaign_id,
        run_id=run_id,
        spec_path=str(spec_path),
        spec_sha256=_sha256_file(spec_path),
        git_commit=_git_commit(),
        stages={
            stage: StageCheckpoint(
                status=("skipped" if routes[stage] == "disabled" else "pending"),
                route=routes[stage],
            )
            for stage in STAGE_ORDER
        },
        preview_required=spec.run_policy.preview_before_push,
        live_delivery_allowed=not spec.run_policy.preview_before_push,
    )


def next_action_for_manifest(manifest: RunManifest) -> NextAction:
    """Return the first incomplete stage, respecting the preview gate."""
    if manifest.preview_required and not manifest.live_delivery_allowed:
        deliver = manifest.stages["deliver"]
        preview = manifest.stages["preview"]
        if deliver.status == "pending" and preview.status != "complete":
            # Keep scanning so preview itself can still be returned when reached.
            pass

    for stage in STAGE_ORDER:
        checkpoint = manifest.stages[stage]
        if checkpoint.status in {"complete", "skipped"}:
            continue
        if stage == "deliver" and manifest.preview_required and not manifest.live_delivery_allowed:
            return NextAction(
                campaign_id=manifest.campaign_id,
                run_id=manifest.run_id,
                stage="deliver",
                route=checkpoint.route,
                status="blocked",
                reason="live delivery is blocked until preview is approved",
            )
        return NextAction(
            campaign_id=manifest.campaign_id,
            run_id=manifest.run_id,
            stage=stage,
            route=checkpoint.route,
            status="ready",
            reason=f"{stage} is the next incomplete stage",
            command_hint=_command_hint(manifest, stage, checkpoint.route),
        )

    return NextAction(
        campaign_id=manifest.campaign_id,
        run_id=manifest.run_id,
        stage=None,
        route=None,
        status="done",
        reason="all stages are complete or skipped",
    )


def next_action_for_run(run_dir: Path) -> NextAction:
    return next_action_for_manifest(load_manifest(run_dir / "manifest.json"))


def _create_run_dirs(run_dir: Path) -> None:
    for child in (
        "",
        "artifacts",
        "prompts",
        "transcripts",
        "quarantine",
        "previews",
        "delivery",
    ):
        (run_dir / child).mkdir(parents=True, exist_ok=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _command_hint(manifest: RunManifest, stage: StageName, route: str) -> str | None:
    if route == "deterministic_cli":
        return f"run deterministic {stage} CLI for runs/{manifest.campaign_id}/{manifest.run_id}"
    if route == "codex_in_session":
        return f"render and complete {stage} judgment in Codex, then write an artifact envelope"
    if route == "browser_chat_skill":
        return f"use the browser/chat skill for {stage}, then save transcript and artifact envelope"
    if route == "manual_review":
        return f"review {stage} output and update the manifest checkpoint"
    return None
