"""Persistent, authorization-gated Apollo resolution with null-result caching."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from .ids import normalize_text, stable_hash
from .state import StateStore


MATCH_URL = "https://api.apollo.io/api/v1/people/match"
FATAL_STATUS_CODES = {400, 401, 402, 403, 422, 429}


class ApolloFatalError(RuntimeError):
    pass


class ApolloTransientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ApolloResult:
    status: str
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    organization: str = ""
    cached: bool = False
    billable: bool = False
    error: str = ""


class ApolloResolver:
    def __init__(
        self,
        state: StateStore,
        api_key: str | None = None,
        request_match: Callable[[str, dict], dict] | None = None,
        null_ttl_days: int = 30,
    ):
        self.state = state
        self.api_key = api_key if api_key is not None else os.environ.get("APOLLO_API_KEY", "")
        self.request_match = request_match or self._request_match
        self.null_ttl_days = null_ttl_days

    def resolve(
        self,
        person: str,
        organization: str,
        *,
        spend: bool = False,
        reveal_phone: bool = False,
        phone_webhook: str = "",
    ) -> ApolloResult:
        normalized_person = normalize_text(person)
        normalized_organization = normalize_text(organization)
        key = stable_hash("apollo", normalized_person, normalized_organization)
        cached = self.state.get_apollo_cache(key)
        if cached:
            payload = json.loads(cached["payload_json"])
            error = json.loads(cached["error_json"]).get("message", "")
            return ApolloResult(
                status=cached["status"],
                email=str(payload.get("email") or ""),
                phone=str(payload.get("phone") or ""),
                linkedin=str(payload.get("linkedin") or ""),
                organization=str(payload.get("organization") or ""),
                cached=True,
                billable=bool(cached["billable"]),
                error=error,
            )
        if not spend:
            return ApolloResult(status="dry_run")
        if not self.api_key:
            raise ApolloFatalError("APOLLO_API_KEY is required when spending is authorized")
        if reveal_phone and not phone_webhook:
            raise ApolloFatalError("phone reveal requires an explicitly authorized webhook URL")
        body = {
            "name": person,
            "organization_name": organization,
            "reveal_personal_emails": True,
            **(
                {
                    "reveal_phone_number": True,
                    "webhook_url": phone_webhook,
                }
                if reveal_phone
                else {}
            ),
        }
        try:
            raw = self.request_match(self.api_key, body)
        except ApolloFatalError as exc:
            self.state.set_apollo_cache(
                key,
                normalized_person,
                normalized_organization,
                "fatal",
                billable=False,
                error={"message": str(exc)},
                expires_at=None,
            )
            raise
        except ApolloTransientError:
            raise
        person_payload = raw.get("person") if isinstance(raw, dict) else None
        parsed = parse_person(person_payload)
        status = "found" if any(parsed.values()) else "null"
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=self.null_ttl_days)
        ).isoformat() if status == "null" else None
        self.state.set_apollo_cache(
            key,
            normalized_person,
            normalized_organization,
            status,
            billable=True,
            payload=parsed,
            expires_at=expires_at,
        )
        return ApolloResult(status=status, cached=False, billable=True, **parsed)

    def _request_match(self, api_key: str, body: dict) -> dict:
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(
                    MATCH_URL,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Cache-Control": "no-cache",
                        "x-api-key": api_key,
                    },
                )
                if response.status_code in FATAL_STATUS_CODES:
                    raise ApolloFatalError(
                        f"Apollo HTTP {response.status_code}: {response.text[:200]}"
                    )
                if response.status_code >= 500:
                    raise ApolloTransientError(
                        f"Apollo HTTP {response.status_code}: {response.text[:200]}"
                    )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise ApolloTransientError(f"Apollo timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise ApolloTransientError(f"Apollo transport error: {exc}") from exc


def parse_person(person: dict | None) -> dict:
    if not person:
        return {"email": "", "phone": "", "linkedin": "", "organization": ""}
    email = str(person.get("email") or "")
    if "email_not_unlocked" in email:
        email = ""
    phone = next(
        (
            str(item.get("sanitized_number") or item.get("raw_number") or "")
            for item in person.get("phone_numbers") or []
            if item.get("sanitized_number") or item.get("raw_number")
        ),
        "",
    )
    return {
        "email": email,
        "phone": phone,
        "linkedin": str(person.get("linkedin_url") or ""),
        "organization": str((person.get("organization") or {}).get("name") or ""),
    }
