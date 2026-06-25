"""Parse browser/chat transcripts into validated enrichment artifacts."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from pipeline.contracts import ArtifactEnvelope
from pipeline.enrich import Lead
from pipeline.grok_parse import parse_grok_response_all


Provider = Literal["grok", "gemini", "manual"]

_FENCED_JSON = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class TranscriptParseResult(BaseModel):
    provider: Provider
    mode: str | None = None
    company_name: str | None = None
    leads: list[Lead] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    raw_text: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_grok_enrichment_transcript(
    text: str,
    *,
    company_name: str | None = None,
    mode: str | None = None,
    max_leads: int = 3,
) -> TranscriptParseResult:
    leads = parse_grok_response_all(text, max_leads=max_leads)
    errors = [] if leads else ["no_leads_parsed"]
    return TranscriptParseResult(
        provider="grok",
        mode=mode,
        company_name=company_name,
        leads=leads,
        errors=errors,
        raw_text=text,
    )


def enrichment_result_to_artifact(
    result: TranscriptParseResult,
    *,
    campaign_id: str,
    run_id: str,
) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        campaign_id=campaign_id,
        run_id=run_id,
        stage="enrich",
        records=[
            {
                "company_name": result.company_name,
                "provider": result.provider,
                "mode": result.mode,
                "lead": asdict(lead),
            }
            for lead in result.leads
        ],
        metadata={
            "provider": result.provider,
            "mode": result.mode,
            "company_name": result.company_name,
            "errors": result.errors,
        },
    )


def extract_first_json_value(text: str) -> Any:
    """Extract the first JSON object/list from a transcript or fenced block."""
    candidates = [m.group(1).strip() for m in _FENCED_JSON.finditer(text)]
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return value
    raise ValueError("no JSON object or array found in transcript")
