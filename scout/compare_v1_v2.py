"""Run frozen V1 and V2 in isolation through the external comparison harness."""
from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from v2.apollo import ApolloResolver
from v2.comparison import (
    ComparisonHarness,
    RuntimeSpec,
    resolve_shared_apollo_union,
    v1_cache_namespace,
    v2_cache_namespace,
)
from v2.state import StateStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-checkout", type=Path, required=True)
    parser.add_argument("--v2-checkout", type=Path, required=True)
    parser.add_argument("--v1-sha", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--source-snapshot", type=Path, required=True)
    parser.add_argument("--stamp", default=date.today().isoformat())
    parser.add_argument("--since", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--apollo-go",
        action="store_true",
        help="authorize shared, union-deduplicated Apollo resolution after both runs",
    )
    args = parser.parse_args(argv)
    args.v1_checkout = args.v1_checkout.resolve()
    args.v2_checkout = args.v2_checkout.resolve()
    args.work_dir = args.work_dir.resolve()
    args.source_snapshot = args.source_snapshot.resolve()
    run_id = args.run_id or str(uuid.uuid4())
    state_root = args.work_dir / "state"
    artifact_root = args.work_dir / "runs" / run_id
    v1 = RuntimeSpec(
        "V1",
        args.v1_checkout,
        ("uv", "run", "scout/pipeline.py"),
        state_root / "v1" / "scout.db",
        artifact_root / "v1" / "results",
        v1_cache_namespace(args.v1_sha, f"comparison-{run_id}"),
        args.v1_sha,
    )
    v2 = RuntimeSpec(
        "V2",
        args.v2_checkout,
        ("uv", "run", "scout/pipeline.py"),
        state_root / "v2" / "scout.db",
        artifact_root / "v2" / "results",
        v2_cache_namespace(f"comparison-{run_id}"),
    )
    shared_db = state_root / "shared-apollo.db"
    harness = ComparisonHarness(
        v1,
        v2,
        shared_apollo_db=shared_db,
        source_snapshot=args.source_snapshot,
        since=args.since,
        stamp=args.stamp,
    )
    preflight = harness.preflight()
    results = harness.run()
    report = {
        "run_id": run_id,
        "preflight": preflight,
        "runtimes": [asdict(result) for result in results],
        "apollo": {"authorized": False},
    }
    if len(results) == 2 and all(result.returncode == 0 for result in results):
        contacts = [spec.results_dir / args.stamp / "contacts.csv" for spec in (v1, v2)]
        if args.apollo_go:
            state = StateStore(shared_db)
            state.migrate()
            report["apollo"] = {
                "authorized": True,
                **resolve_shared_apollo_union(
                    contacts,
                    shared_state=state,
                    resolver=ApolloResolver(state, api_key=os.environ.get("APOLLO_API_KEY", "")),
                    authorize_spend=True,
                ),
            }
            rerendered = harness.rerender_after_shared_projection()
            report["runtimes"] = [asdict(result) for result in rerendered]
            results = rerendered
    report_path = artifact_root / "comparison_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(report_path)
    return 0 if len(results) == 2 and all(result.returncode == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
