"""Manual-only NewsAPI and Apify discovery adapters."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Callable, Protocol
from urllib.parse import quote

import httpx


NEWSAPI_ENDPOINT = "https://eventregistry.org/api/v1/article/getArticles"
NEWSAPI_HARD_MAX_PAGES = 100
NEWSAPI_PAGE_SIZE = 100
PROVIDER_WORKERS = 4
APIFY_RESULTS_PER_QUERY = 20
AEC_QUERY_GROUPS = {
    "openings": "Arizona grand opening commercial property",
    "leases": "Arizona commercial lease tenant signed",
    "occupancy": "Arizona new tenant occupancy move in",
    "construction_completion": "Arizona construction completed commercial project",
    "redevelopment": "Arizona commercial redevelopment adaptive reuse",
    "management_change": "Arizona property management company selected transition",
    "expansion": "Arizona business expansion new facility",
    "multifamily_leaseup": "Arizona apartment lease up opening",
    "industrial_activation": "Arizona warehouse industrial facility opening",
    "retail_hospitality": "Arizona restaurant retail hotel opening",
}


class ProviderPreflightError(RuntimeError):
    pass


@dataclass(slots=True)
class ProviderRecord:
    provider: str
    provider_id: str
    url: str
    title: str
    published_at: str = ""
    source_name: str = ""
    raw: dict | None = None


class ProviderAdapter(Protocol):
    name: str

    def preflight(self) -> None: ...

    def discover(self, start: date, end: date) -> list[ProviderRecord]: ...


class NewsApiAdapter:
    name = "newsapi_ai"

    def __init__(
        self,
        api_key: str | None = None,
        max_pages: int | None = None,
        timeout: int | None = None,
        post_json: Callable[[str, dict, int], dict] | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("NEWSAPI_AI_API_KEY", "")
        configured_pages = os.environ.get("NEWSAPI_AI_MAX_PAGES", "0") if max_pages is None else str(max_pages)
        self.max_pages = NEWSAPI_HARD_MAX_PAGES if int(configured_pages) == 0 else min(
            max(int(configured_pages), 1), NEWSAPI_HARD_MAX_PAGES
        )
        self.timeout = int(timeout or os.environ.get("NEWSAPI_AI_TIMEOUT_SECONDS", "120"))
        self.post_json = post_json or _post_json

    def preflight(self) -> None:
        if not self.api_key.strip():
            raise ProviderPreflightError("NEWSAPI_AI_API_KEY is required")

    def discover(self, start: date, end: date) -> list[ProviderRecord]:
        self.preflight()
        records: dict[str, ProviderRecord] = {}
        for query_name, query in AEC_QUERY_GROUPS.items():
            for page in range(1, self.max_pages + 1):
                payload = {
                    "action": "getArticles",
                    "resultType": "articles",
                    "keyword": query,
                    "keywordSearchMode": "exact",
                    "keywordLoc": "title,body",
                    "sourceLocationUri": "http://en.wikipedia.org/wiki/United_States",
                    "lang": "eng",
                    "dataType": "news",
                    "dateStart": start.isoformat(),
                    "dateEnd": end.isoformat(),
                    "isDuplicateFilter": "skipDuplicates",
                    "articlesSortBy": "date",
                    "articlesSortByAsc": False,
                    "articlesCount": NEWSAPI_PAGE_SIZE,
                    "articlesPage": page,
                    "apiKey": self.api_key,
                }
                response = self.post_json(NEWSAPI_ENDPOINT, payload, self.timeout)
                if response.get("error"):
                    raise RuntimeError(f"NewsAPI error for {query_name}: {response['error']}")
                block = response.get("articles") or {}
                rows = block.get("results") or []
                for row in rows:
                    url = str(row.get("url") or "").strip()
                    if not url:
                        continue
                    records.setdefault(
                        url,
                        ProviderRecord(
                            provider=self.name,
                            provider_id=str(row.get("uri") or row.get("id") or ""),
                            url=url,
                            title=str(row.get("title") or ""),
                            published_at=str(row.get("dateTimePub") or row.get("date") or ""),
                            source_name=str((row.get("source") or {}).get("title") or ""),
                            raw={"query_group": query_name, "article": row},
                        ),
                    )
                pages = int(block.get("pages") or 0)
                if len(rows) < NEWSAPI_PAGE_SIZE or (pages and page >= pages):
                    break
        return list(records.values())


class ApifyFacebookAdapter:
    name = "apify_facebook"

    def __init__(
        self,
        token: str | None = None,
        actor_id: str | None = None,
        timeout: int | None = None,
        run_actor: Callable[[str, str, dict, int], list[dict]] | None = None,
    ):
        self.token = token if token is not None else os.environ.get("APIFY_TOKEN", "")
        self.actor_id = actor_id if actor_id is not None else os.environ.get("APIFY_FACEBOOK_ACTOR_ID", "")
        self.timeout = int(timeout or os.environ.get("APIFY_TIMEOUT_SECONDS", "120"))
        self.run_actor = run_actor or _run_apify_actor

    def preflight(self) -> None:
        missing = [
            name
            for name, value in (("APIFY_TOKEN", self.token), ("APIFY_FACEBOOK_ACTOR_ID", self.actor_id))
            if not value.strip()
        ]
        if missing:
            raise ProviderPreflightError(f"missing provider configuration: {', '.join(missing)}")

    def discover(self, start: date, end: date) -> list[ProviderRecord]:
        self.preflight()
        records: dict[str, ProviderRecord] = {}
        for query_name, query in AEC_QUERY_GROUPS.items():
            payload = {
                "searchQueries": [query],
                "maxPosts": APIFY_RESULTS_PER_QUERY,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            }
            for row in self.run_actor(self.token, self.actor_id, payload, self.timeout)[:APIFY_RESULTS_PER_QUERY]:
                url = str(row.get("url") or row.get("postUrl") or "").strip()
                if not url:
                    continue
                records.setdefault(
                    url,
                    ProviderRecord(
                        provider=self.name,
                        provider_id=str(row.get("id") or row.get("postId") or ""),
                        url=url,
                        title=str(row.get("text") or row.get("title") or "")[:500],
                        published_at=str(row.get("time") or row.get("timestamp") or ""),
                        source_name=str(row.get("pageName") or row.get("userName") or "Facebook"),
                        raw={"query_group": query_name, "post": row},
                    ),
                )
        return list(records.values())


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


def _run_apify_actor(token: str, actor_id: str, payload: dict, timeout: int) -> list[dict]:
    actor = quote(actor_id.replace("/", "~"), safe="~")
    run_url = f"https://api.apify.com/v2/acts/{actor}/runs"
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            run_url,
            params={"token": token, "waitForFinish": timeout},
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        run = (response.json().get("data") or {})
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise RuntimeError("Apify actor did not return a dataset")
        dataset = client.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            params={"token": token, "clean": "true", "format": "json"},
        )
        dataset.raise_for_status()
        value = dataset.json()
        return value if isinstance(value, list) else []
