"""Run Gemini API discovery and write a discover artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.contracts import dump_json_model
from pipeline.gemini_client import GeminiClient
from pipeline.prompts import render_gemini_discovery_prompt
from pipeline.run_state import init_run
from pipeline.source_discovery import (
    gemini_discovery_to_artifact,
    parse_gemini_discovery_transcript,
)
from pipeline.spec import load_campaign_spec_v2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--campaign",
        default=None,
        help="Campaign id or YAML path. Defaults to the cleaning campaign.",
    )
    parser.add_argument("--run-id", default=None, help="Optional run id. Creates/uses runs/<campaign>/<run-id>.")
    parser.add_argument("--run-dir", default=None, help="Existing run directory to write into.")
    parser.add_argument("--max-sources", type=int, default=None, help="Override campaign discovery.max_sources.")
    parser.add_argument("--api-key", default=None, help="Gemini API key. Defaults to GEMINI_API_KEY env var.")
    parser.add_argument("--model", default=None, help="Override campaign Gemini model.")
    parser.add_argument(
        "--no-google-search",
        action="store_true",
        help="Disable Gemini Google Search grounding for this run.",
    )
    args = parser.parse_args([] if argv is None else argv)

    spec = load_campaign_spec_v2(args.campaign)
    run_dir = Path(args.run_dir) if args.run_dir else init_run(args.campaign, run_id=args.run_id)
    _ensure_run_dirs(run_dir)
    run_id = run_dir.name

    max_sources = args.max_sources or spec.sources.max_sources
    prompt = render_gemini_discovery_prompt(spec, max_sources=max_sources)
    prompt_path = run_dir / "prompts" / "gemini-discovery.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    model = args.model or spec.sources.gemini.model
    use_google_search = spec.sources.gemini.use_google_search and not args.no_google_search
    client = GeminiClient(api_key=args.api_key)
    response = client.generate_json(
        model=model,
        prompt=prompt,
        temperature=spec.sources.gemini.temperature,
        use_google_search=use_google_search,
    )

    raw_path = run_dir / "transcripts" / "gemini-discovery-api.json"
    raw_path.write_text(json.dumps(response.raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text_path = run_dir / "transcripts" / "gemini-discovery.txt"
    text_path.write_text(response.text + ("\n" if response.text else ""), encoding="utf-8")

    result = parse_gemini_discovery_transcript(response.text, spec)
    artifact = gemini_discovery_to_artifact(
        result,
        campaign_id=spec.campaign_id,
        run_id=run_id,
    )
    artifact.metadata.update({
        "provider": "gemini_api",
        "model": model,
        "use_google_search": use_google_search,
        "raw_response_path": str(raw_path),
        "prompt_path": str(prompt_path),
    })
    artifact_path = run_dir / "artifacts" / "discover.json"
    dump_json_model(artifact, artifact_path)
    sys.stdout.write(json.dumps(artifact.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    return 0


def _ensure_run_dirs(run_dir: Path) -> None:
    for child in ("artifacts", "prompts", "transcripts", "quarantine", "previews", "delivery"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
