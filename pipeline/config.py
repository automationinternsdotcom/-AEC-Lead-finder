from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path
    source_registry_csv: Path | None
    review_queue_csv: Path | None
    anthropic_api_key: str | None
    anthropic_model: str
    grok_api_key: str | None
    grok_model: str
    dry_run_crm: bool
    auto_create_enabled: bool
    max_sources_per_run: int
    max_pages_per_source: int
    max_articles_per_run: int
    http_timeout_sec: float


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)
    env = os.environ

    def optional_path(key: str) -> Path | None:
        value = env.get(key)
        return Path(value).expanduser() if value else None

    return Settings(
        db_path=Path(env.get("AETHER_DB_PATH", ROOT / "aether.db")).expanduser(),
        source_registry_csv=optional_path("SOURCE_REGISTRY_CSV"),
        review_queue_csv=optional_path("REVIEW_QUEUE_CSV"),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY"),
        anthropic_model=env.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
        grok_api_key=env.get("GROK_API_KEY") or env.get("XAI_API_KEY"),
        grok_model=env.get("GROK_MODEL", "grok-3-mini"),
        dry_run_crm=env.get("DRY_RUN_CRM", "1") != "0",
        auto_create_enabled=env.get("AUTO_CREATE_ENABLED", "0") == "1",
        max_sources_per_run=int(env.get("MAX_SOURCES_PER_RUN", "25")),
        max_pages_per_source=int(env.get("MAX_PAGES_PER_SOURCE", "20")),
        max_articles_per_run=int(env.get("MAX_ARTICLES_PER_RUN", "100")),
        http_timeout_sec=float(env.get("HTTP_TIMEOUT_SEC", "20")),
    )

