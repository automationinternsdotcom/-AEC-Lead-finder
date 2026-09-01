"""External, isolated V1/V2 comparison orchestration and shared Apollo projection."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

from .apollo import ApolloResolver, ApolloResult
from .ids import normalize_text
from .state import SCHEMA_VERSION, StateStore


COMPARISON_GROK_MODEL = "grok-4.3"


class ComparisonPreflightError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    version: str
    checkout: Path
    command: tuple[str, ...]
    database: Path
    results_dir: Path
    cache_namespace: str
    frozen_sha: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    version: str
    returncode: int
    manifest_paths: tuple[str, ...]


CommandRunner = Callable[[Sequence[str], Path, dict[str, str]], int]
HeadResolver = Callable[[Path], str]


def v1_cache_namespace(baseline_sha: str, run_id: str) -> str:
    return f"scout-db-v1-{baseline_sha}-{run_id}"


def v2_cache_namespace(run_id: str, schema_version: int = SCHEMA_VERSION) -> str:
    return f"scout-db-v2-{schema_version}-{run_id}"


class ComparisonHarness:
    def __init__(
        self,
        v1: RuntimeSpec,
        v2: RuntimeSpec,
        *,
        shared_apollo_db: str | Path,
        source_snapshot: str | Path,
        since: str,
        stamp: str,
        runner: CommandRunner | None = None,
        head_resolver: HeadResolver | None = None,
    ):
        self.specs = (v1, v2)
        self.shared_apollo_db = Path(shared_apollo_db)
        self.source_snapshot = Path(source_snapshot)
        self.since = date.fromisoformat(since).isoformat()
        self.stamp = date.fromisoformat(stamp).isoformat()
        self.runner = runner or _run_command
        self.head_resolver = head_resolver or _git_head

    def preflight(self) -> dict:
        v1, v2 = self.specs
        if {v1.version.upper(), v2.version.upper()} != {"V1", "V2"}:
            raise ComparisonPreflightError("comparison requires one V1 and one V2 runtime")
        for spec in self.specs:
            if not spec.checkout.is_dir():
                raise ComparisonPreflightError(f"missing isolated checkout: {spec.checkout}")
            if any(arg == "--apollo-go" or arg.startswith("--apollo-go=") for arg in spec.command):
                raise ComparisonPreflightError(f"{spec.version} command may not spend Apollo credits")
            if spec.database.resolve() == self.shared_apollo_db.resolve():
                raise ComparisonPreflightError("runtime database cannot be the shared Apollo cache")
        if v1.database.resolve() == v2.database.resolve():
            raise ComparisonPreflightError("V1 and V2 require separate databases")
        if v1.cache_namespace == v2.cache_namespace:
            raise ComparisonPreflightError("V1 and V2 cache namespaces overlap")
        if not v1.frozen_sha:
            raise ComparisonPreflightError("V1 requires a frozen baseline SHA")
        if self.head_resolver(v1.checkout) != v1.frozen_sha:
            raise ComparisonPreflightError("V1 checkout does not match the frozen baseline SHA")
        if not v1.cache_namespace.startswith(f"scout-db-v1-{v1.frozen_sha}-"):
            raise ComparisonPreflightError("V1 cache namespace is not baseline-specific")
        if not v2.cache_namespace.startswith(f"scout-db-v2-{SCHEMA_VERSION}-"):
            raise ComparisonPreflightError("V2 cache namespace is not schema-specific")
        if not self.source_snapshot.is_file():
            raise ComparisonPreflightError("shared source snapshot is missing")
        shared = StateStore(self.shared_apollo_db)
        shared.migrate()
        return {
            "source_snapshot_sha256": _sha256(self.source_snapshot),
            "shared_apollo_db": str(self.shared_apollo_db),
            "cache_namespaces": [v1.cache_namespace, v2.cache_namespace],
        }

    def run(self) -> list[RuntimeResult]:
        self.preflight()
        results = []
        for spec in self.specs:
            spec.database.parent.mkdir(parents=True, exist_ok=True)
            spec.results_dir.mkdir(parents=True, exist_ok=True)
            env = self._runtime_env(spec)
            command = [*spec.command, "--since", self.since]
            returncode = self.runner(command, spec.checkout, env)
            native_manifests = sorted(
                (spec.results_dir / self.stamp / "runs").glob("*/manifest.json")
            )
            manifest = _write_runtime_manifest(
                spec,
                stamp=self.stamp,
                since=self.since,
                returncode=returncode,
                source_snapshot=self.source_snapshot,
                native_manifests=native_manifests,
            )
            results.append(RuntimeResult(spec.version.upper(), returncode, (str(manifest),)))
            if returncode:
                break
        return results

    def rerender_after_shared_projection(self) -> list[RuntimeResult]:
        """Rebuild both emails from identically projected contact CSVs and re-manifest."""
        results = []
        for spec in self.specs:
            returncode = self.runner(
                ("uv", "run", "scout/build_email.py", self.stamp),
                spec.checkout,
                self._runtime_env(spec),
            )
            native = sorted(
                (spec.results_dir / self.stamp / "runs").glob("*/manifest.json")
            )
            manifest = _write_runtime_manifest(
                spec,
                stamp=self.stamp,
                since=self.since,
                returncode=returncode,
                source_snapshot=self.source_snapshot,
                native_manifests=native,
            )
            results.append(RuntimeResult(spec.version.upper(), returncode, (str(manifest),)))
            if returncode:
                break
        return results

    def _runtime_env(self, spec: RuntimeSpec) -> dict[str, str]:
        return {
            **os.environ,
            "DB_PATH": str(spec.database),
            "RESULTS_DIR": str(spec.results_dir),
            "NEWS_WEBSITES_CSV": str(self.source_snapshot),
            "SCOUT_COMPARISON_VERSION": spec.version.upper(),
            "SCOUT_COMPARISON_STAMP": self.stamp,
            # Both frozen runtimes must use the same primary model even when a
            # checkout .env or repository environment specifies another value.
            "GROK_MODEL": COMPARISON_GROK_MODEL,
            "EXTRACTOR_MODEL": COMPARISON_GROK_MODEL,
        }


def resolve_shared_apollo_union(
    contact_csvs: Sequence[str | Path],
    *,
    shared_state: StateStore,
    resolver: ApolloResolver,
    authorize_spend: bool,
) -> dict:
    """Resolve each missing person/org once, then project the same result to every CSV."""
    if not authorize_spend:
        raise ComparisonPreflightError("shared Apollo projection requires explicit authorization")
    if resolver.state.path.resolve() != shared_state.path.resolve():
        raise ComparisonPreflightError("Apollo resolver must use the shared comparison cache")
    paths = [Path(path) for path in contact_csvs]
    rows_by_path = {path: _read_csv(path) for path in paths}
    unresolved: dict[tuple[str, str], tuple[str, str]] = {}
    for rows in rows_by_path.values():
        for row in rows:
            if any(str(row.get(field) or "").strip() for field in ("email", "phone", "linkedin")):
                continue
            person = str(row.get("person") or row.get("person_name") or "").strip()
            organization = str(row.get("business_name") or row.get("organization") or "").strip()
            if person and organization:
                unresolved[(normalize_text(person), normalize_text(organization))] = (
                    person,
                    organization,
                )
    resolved: dict[tuple[str, str], ApolloResult] = {}
    for key, identity in sorted(unresolved.items()):
        resolved[key] = resolver.resolve(*identity, spend=True)
    projected = 0
    for path, rows in rows_by_path.items():
        for row in rows:
            key = (
                normalize_text(str(row.get("person") or row.get("person_name") or "")),
                normalize_text(str(row.get("business_name") or row.get("organization") or "")),
            )
            result = resolved.get(key)
            if not result:
                continue
            changed = False
            for field in ("email", "phone", "linkedin"):
                if not str(row.get(field) or "").strip() and getattr(result, field):
                    row[field] = getattr(result, field)
                    changed = True
            if "apollo_status" not in row:
                row["apollo_status"] = result.status
            if changed:
                projected += 1
        _atomic_csv(path, rows)
    return {
        "unique_unresolved_identities": len(unresolved),
        "cache_entries": len(resolved),
        "projected_rows": projected,
    }


def comparison_subject(stamp: str, version: str, suffix: str) -> str:
    parsed = date.fromisoformat(stamp)
    label = version.upper()
    if label not in {"V1", "V2"}:
        raise ValueError("comparison subject version must be V1 or V2")
    return f"Aether AEC Lead Crawl {parsed.month}/{parsed.day} [{label}] - {suffix}"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def _atomic_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


def _run_command(command: Sequence[str], cwd: Path, env: dict[str, str]) -> int:
    return subprocess.run(list(command), cwd=cwd, env=env, check=False).returncode


def _git_head(checkout: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_runtime_manifest(
    spec: RuntimeSpec,
    *,
    stamp: str,
    since: str,
    returncode: int,
    source_snapshot: Path,
    native_manifests: Sequence[Path],
) -> Path:
    day_dir = spec.results_dir / stamp
    day_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = day_dir / "comparison_manifest.json"
    artifacts = []
    for path in sorted(day_dir.rglob("*")):
        if path.is_file() and path != manifest_path and not path.name.endswith(".tmp"):
            artifacts.append(
                {"path": str(path), "sha256": _sha256(path), "byte_count": path.stat().st_size}
            )
    payload = {
        "comparison_manifest_version": 1,
        "version": spec.version.upper(),
        "status": "completed" if returncode == 0 else "failed",
        "returncode": returncode,
        "stamp": stamp,
        "since": since,
        "frozen_sha": spec.frozen_sha,
        "cache_namespace": spec.cache_namespace,
        "models": {"primary": COMPARISON_GROK_MODEL},
        "source_snapshot": {
            "path": str(source_snapshot),
            "sha256": _sha256(source_snapshot),
        },
        "native_manifests": [str(path) for path in native_manifests],
        "artifacts": artifacts,
    }
    temp = manifest_path.with_name(manifest_path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, manifest_path)
    return manifest_path
