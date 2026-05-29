from __future__ import annotations

import json
from typing import Protocol

import httpx

from schema import EnrichmentResult, ExtractionResult
from pipeline.config import Settings


class Enricher(Protocol):
    def enrich(self, extraction: ExtractionResult) -> EnrichmentResult:
        ...


class NoOpEnricher:
    def enrich(self, extraction: ExtractionResult) -> EnrichmentResult:
        return EnrichmentResult(
            enrichment_confidence=0,
            needs_human_review=True,
            evidence=[],
        )


class GrokEnricher:
    def __init__(self, settings: Settings) -> None:
        if not settings.grok_api_key:
            raise RuntimeError("GROK_API_KEY or XAI_API_KEY is required for Grok enrichment")
        self.settings = settings

    def enrich(self, extraction: ExtractionResult) -> EnrichmentResult:
        prompt = _build_prompt(extraction)
        headers = {
            "Authorization": f"Bearer {self.settings.grok_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.settings.grok_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "Return only JSON that matches the requested schema."},
                {"role": "user", "content": prompt},
            ],
        }
        with httpx.Client(timeout=self.settings.http_timeout_sec) as client:
            response = client.post("https://api.x.ai/v1/chat/completions", headers=headers, json=body)
            response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]
        return EnrichmentResult.model_validate(_json_from_text(content))


def _build_prompt(extraction: ExtractionResult) -> str:
    payload = {
        "property_name": extraction.property_name,
        "address": extraction.address,
        "property_type": extraction.property_type,
        "source_url": extraction.source_url,
        "article_facts": extraction.model_dump(mode="json"),
        "missing_fields": _missing_fields(extraction),
    }
    return (
        "Find only public, evidence-backed company/contact enrichment for this Aether lead. "
        "Do not guess. Keep article facts separate from enriched facts. "
        "Return JSON with owner_company, developer_company, property_manager_company, "
        "tenant_or_operator, best_contact, evidence, enrichment_confidence, and needs_human_review.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )


def _missing_fields(extraction: ExtractionResult) -> list[str]:
    fields = []
    for field_name in ("contact_name", "contact_title", "contact_email", "contact_phone"):
        if getattr(extraction, field_name) is None:
            fields.append(field_name)
    return fields


def _json_from_text(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.removeprefix("json").strip()
    return json.loads(stripped)

