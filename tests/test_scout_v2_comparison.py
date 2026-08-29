"""The comparison harness isolates runtimes and centralizes Apollo spending."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.apollo import ApolloResolver  # noqa: E402
from v2.comparison import (  # noqa: E402
    ComparisonHarness,
    ComparisonPreflightError,
    RuntimeSpec,
    comparison_subject,
    resolve_shared_apollo_union,
    v1_cache_namespace,
    v2_cache_namespace,
)
from v2.state import StateStore  # noqa: E402


def spec(tmp_path, version, command=("uv", "run", "scout/pipeline.py")):
    checkout = tmp_path / version
    checkout.mkdir(exist_ok=True)
    return RuntimeSpec(
        version=version,
        checkout=checkout,
        command=command,
        database=tmp_path / f"{version}.db",
        results_dir=tmp_path / f"results-{version}",
        cache_namespace=(
            v1_cache_namespace("abc123", "run-1") if version == "V1" else v2_cache_namespace("run-1")
        ),
        frozen_sha="abc123" if version == "V1" else "",
    )


def test_harness_enforces_frozen_v1_no_spend_and_separate_namespaces(tmp_path):
    source = tmp_path / "sources.csv"
    source.write_text("name,url\nExample,https://example.com\n")
    calls = []
    harness = ComparisonHarness(
        spec(tmp_path, "V1"),
        spec(tmp_path, "V2"),
        shared_apollo_db=tmp_path / "shared.db",
        source_snapshot=source,
        since="2026-08-27",
        stamp="2026-08-28",
        runner=lambda command, cwd, env: calls.append((command, cwd, env)) or 0,
        head_resolver=lambda checkout: "abc123",
    )
    report = harness.preflight()
    assert report["source_snapshot_sha256"]
    results = harness.run()
    assert len(results) == 2 and len(calls) == 2
    assert all(Path(result.manifest_paths[0]).exists() for result in results)
    assert all("--apollo-go" not in call[0] for call in calls)
    assert calls[0][2]["DB_PATH"] != calls[1][2]["DB_PATH"]

    bad = spec(tmp_path, "V1", command=("python", "pipeline.py", "--apollo-go"))
    with pytest.raises(ComparisonPreflightError):
        ComparisonHarness(
            bad,
            spec(tmp_path, "V2"),
            shared_apollo_db=tmp_path / "shared-2.db",
            source_snapshot=source,
            since="2026-08-27",
            stamp="2026-08-28",
            head_resolver=lambda checkout: "abc123",
        ).preflight()


def write_contacts(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_shared_apollo_resolves_union_once_and_projects_identically(tmp_path):
    paths = [tmp_path / "v1.csv", tmp_path / "v2.csv"]
    row = {"business_name": "Acme", "person": "Jane Doe", "email": "", "phone": "", "linkedin": ""}
    for path in paths:
        write_contacts(path, [row])
    state = StateStore(tmp_path / "shared.db")
    state.migrate()
    calls = []
    resolver = ApolloResolver(
        state,
        api_key="test",
        request_match=lambda key, body: calls.append(body) or {"person": {"email": "jane@acme.example"}},
    )
    report = resolve_shared_apollo_union(
        paths, shared_state=state, resolver=resolver, authorize_spend=True
    )
    assert len(calls) == 1 and report["unique_unresolved_identities"] == 1
    assert all("jane@acme.example" in path.read_text() for path in paths)
    assert comparison_subject("2026-08-28", "v2", "2 priority") == (
        "Aether AEC Lead Crawl 8/28 [V2] - 2 priority"
    )


def test_shared_apollo_requires_explicit_authorization(tmp_path):
    state = StateStore(tmp_path / "shared.db")
    state.migrate()
    with pytest.raises(ComparisonPreflightError):
        resolve_shared_apollo_union(
            [], shared_state=state, resolver=ApolloResolver(state), authorize_spend=False
        )
