"""Versioned contracts for Phase 2 run artifacts.

These models are deliberately small in 2A. They give every stage a shared
envelope and every run a manifest without pretending Python can execute the
Codex/browser reasoning stages by itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


StageName = Literal[
    "fetch",
    "extract",
    "pattern",
    "qualify",
    "enrich",
    "preview",
    "deliver",
]

StageStatusValue = Literal["pending", "running", "complete", "failed", "skipped"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactEnvelope(BaseModel):
    """Shared JSON shape for stage outputs under runs/.../artifacts."""
    schema_version: Literal["artifact_envelope.v1"] = "artifact_envelope.v1"
    campaign_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage: StageName
    created_at: datetime = Field(default_factory=utc_now)
    records: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _record_count_matches_metadata(self) -> "ArtifactEnvelope":
        count = self.metadata.get("record_count")
        if count is not None and count != len(self.records):
            raise ValueError("metadata.record_count must match len(records)")
        self.metadata.setdefault("record_count", len(self.records))
        return self


class StageCheckpoint(BaseModel):
    status: StageStatusValue = "pending"
    route: str
    artifact_path: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)
    message: str | None = None


class RunManifest(BaseModel):
    """Run-level state. Checkpoints can be updated one stage at a time."""
    schema_version: Literal["run_manifest.v1"] = "run_manifest.v1"
    campaign_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    spec_path: str
    spec_sha256: str
    git_commit: str | None = None
    stages: dict[StageName, StageCheckpoint]
    counts: dict[str, int] = Field(default_factory=dict)
    anomalies: list[str] = Field(default_factory=list)
    preview_required: bool = True
    live_delivery_allowed: bool = False


class NextAction(BaseModel):
    """What the human/Codex orchestrator should do next."""
    campaign_id: str
    run_id: str
    stage: StageName | None
    route: str | None
    status: Literal["ready", "blocked", "done"]
    reason: str
    command_hint: str | None = None


def dump_json_model(model: BaseModel, path: Path) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_manifest(path: Path) -> RunManifest:
    return RunManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_artifact(path: Path) -> ArtifactEnvelope:
    return ArtifactEnvelope.model_validate(json.loads(path.read_text(encoding="utf-8")))
