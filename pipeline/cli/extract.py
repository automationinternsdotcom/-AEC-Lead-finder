"""`python -m pipeline.cli.extract <url>` — print cleaned article text on stdout.

Exit codes: 0 = ok, 1 = ExtractError (paywall/short/HTTP), 2 = bad CLI args.
"""
from __future__ import annotations

import sys

from pipeline import extract, util


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python -m pipeline.cli.extract <url>\n")
        return 2

    url = sys.argv[1]
    try:
        with util.make_http_client() as http:
            text = extract.extract_article_text(url, http)
    except extract.ExtractError as e:
        sys.stderr.write(f"{e}\n")
        return 1

    sys.stdout.write(text)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
