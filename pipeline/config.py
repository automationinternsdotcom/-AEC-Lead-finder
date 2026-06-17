"""Env + YAML config loader. One Settings instance per process via lru_cache.

Reuses: python-dotenv, pyyaml, dataclass, functools.lru_cache.
Extend: add a field to Settings, then add one `need()`/`env.get()` line in settings().
Required env vars are listed in .env.example.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SOURCES_YAML = ROOT / "sources.yaml"
RATES_YAML = ROOT / "rates.yaml"

# Static defaults — change in code, not via env.
HTTP_TIMEOUT_SEC = 15


@dataclass(frozen=True, slots=True)
class Settings:
    pipedrive_api_token: str
    pipedrive_domain: str
    # Pipedrive shares custom field hashes between Lead and Deal entities, so the
    # same field key works for either. Required for push.py's custom field write.
    pipedrive_field_article_url: str
    # Optional extras populated by push.py when set. Leaving them None makes the
    # push skip the field — useful for environments that haven't created them yet.
    pipedrive_field_date_posted: str | None = None
    pipedrive_field_lead_1: str | None = None
    pipedrive_field_lead_2: str | None = None
    pipedrive_field_lead_3: str | None = None
    apollo_api_key: str | None = None
    # Email digest (pipeline.cli.email_digest). All optional — the digest CLI
    # errors clearly when a send is attempted without host + to + from set.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    lead_digest_to: str | None = None
    lead_digest_from: str | None = None
    dry_run: bool = False
    max_articles_per_run: int = 50
    log_level: str = "INFO"
    dedup_window_days: int = 14
    dedup_score_threshold: float = 0.5


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Load .env once and freeze a Settings instance for the process lifetime."""
    load_dotenv(ROOT / ".env", override=False)
    env = os.environ

    def need(key: str) -> str:
        v = env.get(key)
        if not v:
            raise RuntimeError(f"Missing required env var: {key}")
        return v

    return Settings(
        pipedrive_api_token=need("PIPEDRIVE_API_TOKEN"),
        pipedrive_domain=need("PIPEDRIVE_DOMAIN"),
        pipedrive_field_article_url=need("PIPEDRIVE_FIELD_ARTICLE_URL"),
        pipedrive_field_date_posted=env.get("PIPEDRIVE_FIELD_DATE_POSTED") or None,
        pipedrive_field_lead_1=env.get("PIPEDRIVE_FIELD_LEAD_1") or None,
        pipedrive_field_lead_2=env.get("PIPEDRIVE_FIELD_LEAD_2") or None,
        pipedrive_field_lead_3=env.get("PIPEDRIVE_FIELD_LEAD_3") or None,
        apollo_api_key=env.get("APOLLO_API_KEY") or None,
        smtp_host=env.get("SMTP_HOST") or None,
        smtp_port=int(env.get("SMTP_PORT") or 587),
        smtp_user=env.get("SMTP_USER") or None,
        smtp_password=env.get("SMTP_PASSWORD") or None,
        lead_digest_to=env.get("LEAD_DIGEST_TO") or None,
        lead_digest_from=env.get("LEAD_DIGEST_FROM") or None,
        dry_run=env.get("DRY_RUN", "0") == "1",
        max_articles_per_run=int(env.get("MAX_ARTICLES_PER_RUN") or 50),
        log_level=env.get("LOG_LEVEL", "INFO"),
        dedup_window_days=int(env.get("DEDUP_WINDOW_DAYS") or 14),
        dedup_score_threshold=float(env.get("DEDUP_SCORE_THRESHOLD") or 0.5),
    )


def load_sources() -> list[dict]:
    """sources.yaml → list of {name, method, endpoint, enabled} dicts."""
    return yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8")) or []


def load_rates() -> dict[str, float]:
    """rates.yaml → {property_type: $/sqft/month or $/unit/month for multifamily}."""
    return yaml.safe_load(RATES_YAML.read_text(encoding="utf-8")) or {}
