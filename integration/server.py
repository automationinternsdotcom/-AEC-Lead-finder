"""Local-only API entrypoint that lets Python parse the dotenv file safely."""

from __future__ import annotations

import os

import uvicorn

from .config import Settings


def main() -> int:
    settings = Settings.from_env()
    port = int(os.environ.get("AETHER_SALES_PORT", "8080"))
    uvicorn.run(
        "integration.api:app",
        host="127.0.0.1",
        port=port,
        log_level=settings.log_level.casefold(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
