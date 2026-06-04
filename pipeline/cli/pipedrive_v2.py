"""Dry-run Pipedrive cleanup/schema/pipeline/automation planning."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date

from pipeline import automations, enrich, pipedrive_v2
from schema import ExtractedArticle


def _read_json(path: str | None):
    if not path or path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    schema = sub.add_parser("schema")
    schema.add_argument("--existing-json")
    pipeline = sub.add_parser("pipeline")
    pipeline.add_argument("--existing-pipelines-json")
    pipeline.add_argument("--existing-stages-json")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--input", default="-")
    preview = sub.add_parser("automation-preview")
    preview.add_argument("--input", default="-")
    args = parser.parse_args()

    if args.cmd == "schema":
        existing = _read_json(args.existing_json) if args.existing_json else []
        out = pipedrive_v2.schema_plan(existing)
    elif args.cmd == "pipeline":
        pipelines = _read_json(args.existing_pipelines_json) if args.existing_pipelines_json else []
        stages = _read_json(args.existing_stages_json) if args.existing_stages_json else []
        out = pipedrive_v2.pipeline_plan(pipelines, stages)
    elif args.cmd == "cleanup":
        out = pipedrive_v2.cleanup_report(_read_json(args.input))
    elif args.cmd == "automation-preview":
        raw = _read_json(args.input)
        article = ExtractedArticle.model_validate(raw["article"])
        lead_raw = raw.get("lead")
        lead = enrich.Lead(**lead_raw) if lead_raw else None
        out = automations.build_followup_activities(
            lead_id=raw.get("lead_id", "preview-lead-id"),
            org_id=raw.get("org_id"),
            person_id=raw.get("person_id"),
            article=article,
            primary=lead,
            url=raw["url"],
            owner_id=raw.get("owner_id"),
            today=date.fromisoformat(raw["today"]) if raw.get("today") else None,
        )
        out = [dict(p) for p in out]
    else:  # pragma: no cover - argparse guards this
        raise AssertionError(args.cmd)

    json.dump(out, sys.stdout, default=dataclasses.asdict)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
