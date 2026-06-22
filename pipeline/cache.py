"""Persona-aware enrichment cache helpers for Phase 2.

The legacy SQLite cache keys only by organization name. Phase 2 needs the
cache key to include campaign + buyer persona so different products can target
different contacts at the same company without cross-contaminating results.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict

from pydantic import BaseModel, Field

from pipeline.enrich import Lead
from pipeline.util import utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS enrichment_cache_v2 (
  cache_key       TEXT PRIMARY KEY,
  campaign_id     TEXT NOT NULL,
  org_normalized  TEXT NOT NULL,
  persona_key     TEXT NOT NULL,
  raw_org_name    TEXT NOT NULL,
  lead_json       TEXT NOT NULL,
  source          TEXT NOT NULL,
  enriched_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enrichment_cache_v2_org
  ON enrichment_cache_v2(org_normalized);
"""

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_MULTI_SPACE = re.compile(r"\s+")
_BUSINESS_SUFFIXES = (
    "llc", "l l c", "inc", "incorporated", "corp", "corporation",
    "ltd", "limited", "lp", "l p", "llp", "l l p", "company", "co",
)
_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in _BUSINESS_SUFFIXES) + r")\b",
    re.IGNORECASE,
)


class PersonaCacheKey(BaseModel):
    campaign_id: str = Field(min_length=1)
    org_normalized: str = Field(min_length=1)
    persona_key: str = Field(min_length=1)

    @property
    def value(self) -> str:
        return f"{self.campaign_id}:{self.org_normalized}:{self.persona_key}"


def ensure_cache_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def normalize_org_name(name: str) -> str:
    s = name.lower()
    s = _NON_ALNUM.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    s = _SUFFIX_RE.sub(" ", s)
    return _MULTI_SPACE.sub(" ", s).strip()


def normalize_persona(persona: str) -> str:
    s = persona.lower()
    s = _NON_ALNUM.sub(" ", s)
    return _MULTI_SPACE.sub(" ", s).strip()


def build_persona_cache_key(campaign_id: str, org_name: str, persona: str) -> PersonaCacheKey:
    return PersonaCacheKey(
        campaign_id=campaign_id,
        org_normalized=normalize_org_name(org_name),
        persona_key=normalize_persona(persona),
    )


def get_cached_leads(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    org_name: str,
    persona: str,
) -> list[Lead]:
    ensure_cache_schema(conn)
    key = build_persona_cache_key(campaign_id, org_name, persona)
    row = conn.execute(
        "SELECT lead_json FROM enrichment_cache_v2 WHERE cache_key = ?",
        (key.value,),
    ).fetchone()
    if row is None:
        return []
    return [Lead(**item) for item in json.loads(row["lead_json"])]


def cache_leads(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    org_name: str,
    persona: str,
    leads: list[Lead],
    source: str,
) -> None:
    ensure_cache_schema(conn)
    key = build_persona_cache_key(campaign_id, org_name, persona)
    conn.execute(
        "INSERT OR REPLACE INTO enrichment_cache_v2 "
        "(cache_key, campaign_id, org_normalized, persona_key, raw_org_name, "
        "lead_json, source, enriched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            key.value,
            key.campaign_id,
            key.org_normalized,
            key.persona_key,
            org_name,
            json.dumps([asdict(lead) for lead in leads]),
            source,
            utc_now_iso(),
        ),
    )
