"""Create an auditable daily promotion scorecard from deterministic inputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import llm
from v2.promotion import PromotionInputs, build_scorecard


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--final-dir", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument(
        "--human-override",
        type=Path,
        help="optional JSON object with decision, signed_by, and reason",
    )
    args = parser.parse_args(argv)
    inputs = PromotionInputs(**json.loads(args.inputs.read_text(encoding="utf-8")))
    card = build_scorecard(
        inputs,
        final_dir=args.final_dir,
        input_artifacts=args.artifact,
        human_override=(
            json.loads(args.human_override.read_text(encoding="utf-8"))
            if args.human_override
            else None
        ),
        judge_call=lambda prompt, model, temperature: llm.call(
            model,
            prompt,
            text_format="json_object",
            temperature=temperature,
        ),
    )
    print(json.dumps(card, indent=2, sort_keys=True))
    return 0 if not card["manual_review_required"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
