"""Configuration for the GPS-style AEC scout pipeline."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ENV = dotenv_values(REPO / ".env")


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key) or ENV.get(key) or default


def _path(value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else REPO / path)


BASE_URL = _get("CLIPROXY_BASE_URL", "http://localhost:8317/v1")
API_KEY = _get("CLIPROXY_API_KEY", "ado-local-dev")
GROK_MODEL = _get("GROK_MODEL", "grok-4.3")
EXTRACTOR_MODEL = _get("EXTRACTOR_MODEL", "grok-3-mini")
DB_PATH = _path(_get("DB_PATH", "scout.db"))
RESULTS_DIR = _path(_get("RESULTS_DIR", "results"))
NEWS_WEBSITES_CSV = _path(_get("NEWS_WEBSITES_CSV", "news_websites.csv"))

PIPEDRIVE_API_TOKEN = _get("PIPEDRIVE_API_TOKEN")
PIPEDRIVE_DOMAIN = _get("PIPEDRIVE_DOMAIN", "aether")
PIPEDRIVE_FIELD_ARTICLE_URL = _get("PIPEDRIVE_FIELD_ARTICLE_URL")
PIPEDRIVE_ARTICLE_DEAL_PIPELINE_ID = int(_get("PIPEDRIVE_ARTICLE_DEAL_PIPELINE_ID", "47"))
PIPEDRIVE_ARTICLE_DEAL_STAGE_ID = int(_get("PIPEDRIVE_ARTICLE_DEAL_STAGE_ID", "311"))
