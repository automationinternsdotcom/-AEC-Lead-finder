"""`python -m pipeline.cli.qualify` (stdin JSON) — exit 0 if qualifies, 1 if drops.

Reads an ExtractedArticle JSON document from stdin, validates via pydantic,
checks drop rules. On drop, writes the reason string to stderr.
"""
from __future__ import annotations

import json
import sys

from pydantic import ValidationError

from pipeline import extract
from schema import ExtractedArticle


def main() -> int:
    raw = sys.stdin.read()
    try:
        article = ExtractedArticle.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        sys.stderr.write(f"invalid_extracted_article: {e}\n")
        return 2

    passes, reason = extract.is_qualifying(article)
    if not passes:
        sys.stderr.write(f"{reason}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
