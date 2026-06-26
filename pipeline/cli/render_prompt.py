"""Render deterministic CampaignSpec-driven prompts.

Examples:
  uv run python -m pipeline.cli.render_prompt assess
  uv run python -m pipeline.cli.render_prompt grok-fast --company-name Acme
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline import prompts
from pipeline.spec import load_campaign_spec, load_campaign_spec_v2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "kind",
        choices=(
            "assess",
            "grok-fast",
            "grok-expert",
            "entity-adjudication",
            "gemini-discovery",
        ),
        help="Prompt to render.",
    )
    parser.add_argument(
        "--campaign",
        default=None,
        help="Campaign id or YAML path. Defaults to the cleaning campaign.",
    )
    parser.add_argument("--company-name")
    parser.add_argument("--city")
    parser.add_argument("--description")
    parser.add_argument("--owner-entity")
    parser.add_argument("--article-summary")
    parser.add_argument("--article-url")
    parser.add_argument(
        "--fast-findings",
        default="",
        help="Fast-mode findings block for grok-expert.",
    )
    parser.add_argument(
        "--candidate-json",
        help="Path to an ambiguous pattern candidate JSON record for entity-adjudication.",
    )
    parser.add_argument(
        "--max-sources",
        type=int,
        default=None,
        help="Maximum source URLs to ask Gemini for. Defaults to the campaign spec.",
    )
    args = parser.parse_args([] if argv is None else argv)

    if args.kind == "assess":
        spec = load_campaign_spec(args.campaign)
        rendered = prompts.render_assess_prompt(spec)
    elif args.kind in {"grok-fast", "grok-expert"}:
        spec = load_campaign_spec(args.campaign)
        if not args.company_name:
            parser.error(f"{args.kind} requires --company-name")
        common = {
            "company_name": args.company_name,
            "city": args.city,
            "description": args.description,
            "owner_entity": args.owner_entity,
            "article_summary": args.article_summary,
            "article_url": args.article_url,
        }
        if args.kind == "grok-fast":
            rendered = prompts.render_grok_fast_prompt(spec, **common)
        else:
            rendered = prompts.render_grok_expert_prompt(
                spec,
                fast_findings_block=args.fast_findings,
                **common,
            )
    elif args.kind == "entity-adjudication":
        spec = load_campaign_spec(args.campaign)
        if not args.candidate_json:
            parser.error("entity-adjudication requires --candidate-json")
        candidate = json.loads(Path(args.candidate_json).read_text(encoding="utf-8"))
        rendered = prompts.render_entity_adjudication_prompt(spec, candidate=candidate)
    else:
        spec_v2 = load_campaign_spec_v2(args.campaign)
        rendered = prompts.render_gemini_discovery_prompt(
            spec_v2,
            max_sources=args.max_sources or spec_v2.sources.max_sources,
        )

    sys.stdout.write(rendered)
    if not rendered.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
