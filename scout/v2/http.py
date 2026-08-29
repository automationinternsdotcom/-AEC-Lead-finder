"""Public HTTP boundary for Scout V2 discovery and verification."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import httpx


BROWSER_UA = "Mozilla/5.0 (compatible; AetherAECScout/2.0)"


@dataclass(slots=True)
class FetchResponse:
    url: str
    content: bytes
    status_code: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class HttpFetcher:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def __call__(self, url: str) -> FetchResponse:
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": BROWSER_UA},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            return FetchResponse(
                url=str(response.url),
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                history=[str(item.url) for item in response.history],
            )
