"""Atomic raw/final artifact and manifest persistence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from .contracts import RunManifest, StageStatus, utc_now
from .state import StateStore


class ArtifactStore:
    def __init__(self, results_dir: str | Path, stamp: str, run_id: str, state: StateStore):
        self.run_id = run_id
        self.state = state
        self.run_dir = Path(results_dir) / stamp / "runs" / run_id
        self.raw_dir = self.run_dir / "raw"
        self.final_dir = self.run_dir / "final"
        self.manifest_path = self.run_dir / "manifest.json"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.final_dir.mkdir(parents=True, exist_ok=True)

    def write_raw(self, stage: str, name: str, value: object) -> dict:
        return self._write(stage, "raw", self.raw_dir / name, _json_bytes(value))

    def write_raw_text(self, stage: str, name: str, value: str) -> dict:
        return self._write(stage, "raw", self.raw_dir / name, value.encode("utf-8"))

    def write_json(self, stage: str, name: str, value: object) -> dict:
        return self._write(stage, "final", self.final_dir / name, _json_bytes(value))

    def write_jsonl(self, stage: str, name: str, rows: Iterable[BaseModel | dict]) -> dict:
        payload = b"".join(
            json.dumps(
                row.model_dump(mode="json") if isinstance(row, BaseModel) else row,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            + b"\n"
            for row in rows
        )
        return self._write(stage, "final", self.final_dir / name, payload)

    def write_manifest(self, manifest: RunManifest) -> dict:
        manifest.updated_at = utc_now()
        artifact = self._write(
            "manifest",
            "manifest",
            self.manifest_path,
            _json_bytes(manifest.model_dump(mode="json")),
            record=False,
        )
        self.state.set_run_status(self.run_id, manifest.status, str(self.manifest_path))
        return artifact

    def load_manifest(self) -> RunManifest:
        return RunManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))

    def verify_stage(self, stage: str) -> None:
        """Fail closed when a completed stage's durable artifacts changed."""
        for artifact in self.state.artifacts_for_run(self.run_id):
            if artifact["stage"] != stage:
                continue
            path = Path(artifact["path"])
            if not path.is_file():
                raise ValueError(
                    f"cannot resume completed stage {stage}: artifact missing: {path}"
                )
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if digest != artifact["sha256"] or len(payload) != artifact["byte_count"]:
                raise ValueError(
                    f"cannot resume completed stage {stage}: artifact integrity mismatch: {path}"
                )

    def record_existing(self, stage: str, kind: str, path: str | Path) -> dict:
        target = Path(path)
        payload = target.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        artifact = {
            "stage": stage,
            "kind": kind,
            "path": str(target),
            "sha256": digest,
            "byte_count": len(payload),
        }
        self.state.record_artifact(
            self.run_id, stage, kind, str(target), digest, len(payload)
        )
        return artifact

    def _write(
        self,
        stage: str,
        kind: str,
        path: Path,
        payload: bytes,
        *,
        record: bool = True,
    ) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        with temp.open("wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        digest = hashlib.sha256(payload).hexdigest()
        artifact = {
            "stage": stage,
            "kind": kind,
            "path": str(path),
            "sha256": digest,
            "byte_count": len(payload),
        }
        if record:
            self.state.record_artifact(
                self.run_id, stage, kind, str(path), digest, len(payload)
            )
        return artifact


def new_manifest(run_id: str, stamp: str, since: str, configuration: dict) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        stamp=stamp,
        since=since,
        status=StageStatus.PENDING,
        configuration=configuration,
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, default=str, ensure_ascii=False) + "\n"
    ).encode("utf-8")
