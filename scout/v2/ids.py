"""Stable identity helpers shared by every V2 stage."""
from __future__ import annotations

import hashlib
import re
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


NAMESPACE = uuid.UUID("83dfbcb1-c966-4b29-94bd-4d7747288992")
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}


def normalize_text(value: str) -> str:
    """Collapse punctuation and whitespace for deterministic identity inputs."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def canonicalize_url(value: str) -> str:
    """Return a conservative canonical HTTP(S) URL without tracking parameters."""
    raw = str(value or "").strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"not an HTTP(S) URL: {value!r}")
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and not ((parts.scheme.lower() == "http" and port == 80) or (parts.scheme.lower() == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
        ),
        doseq=True,
    )
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def stable_uuid(kind: str, *parts: object) -> str:
    material = "\x1f".join([normalize_text(kind), *(normalize_text(str(part)) for part in parts)])
    return str(uuid.uuid5(NAMESPACE, material))


def stable_hash(*parts: object) -> str:
    material = "\x1f".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def candidate_id(provider: str, provider_id: str, url: str) -> str:
    identity = provider_id.strip() if provider_id else canonicalize_url(url)
    return stable_uuid("candidate", provider, identity)


def organization_id(name: str, domain: str = "", location: str = "") -> str:
    return stable_uuid("organization", name, domain, location)


def person_id(name: str, organization: str) -> str:
    return stable_uuid("person", name, organization)


def event_id(organization: str, event: str, location: str, event_date: str = "") -> str:
    return stable_uuid("lead-event", organization, event, location, event_date)
