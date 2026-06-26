"""Thin Gemini API client for discovery prompts."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from pipeline.config import HTTP_TIMEOUT_SEC

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


@dataclass(frozen=True)
class GeminiResponse:
    raw: dict[str, Any]
    text: str


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = GEMINI_API_BASE,
        timeout: float = HTTP_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini API discovery")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate_json(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.1,
        use_google_search: bool = True,
    ) -> GeminiResponse:
        url = f"{self.base_url}/models/{model}:generateContent"
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }
        if use_google_search:
            payload["tools"] = [{"google_search": {}}]

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, params={"key": self.api_key}, json=payload)
            response.raise_for_status()
            raw = response.json()
        return GeminiResponse(raw=raw, text=extract_text(raw))


def extract_text(raw: dict[str, Any]) -> str:
    """Extract joined text parts from a Gemini generateContent response."""
    chunks: list[str] = []
    for candidate in raw.get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts", []):
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunk for chunk in chunks if chunk).strip()
