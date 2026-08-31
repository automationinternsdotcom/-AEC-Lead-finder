"""Stable person-level identity keys for the Scout integration boundary."""

from __future__ import annotations

import re
import uuid

NAMESPACE = uuid.UUID("83dfbcb1-c966-4b29-94bd-4d7747288992")


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def stable_uuid(kind: str, *parts: object) -> str:
    material = "\x1f".join(
        [normalize_text(kind), *(normalize_text(str(part)) for part in parts)]
    )
    return str(uuid.uuid5(NAMESPACE, material))


def organization_id(name: str, domain: str = "", location: str = "") -> str:
    return stable_uuid("organization", name, domain, location)


def person_id(name: str, organization: str) -> str:
    return stable_uuid("person", name, organization)


def event_id(organization: str, event: str, location: str, event_date: str = "") -> str:
    return stable_uuid("lead-event", organization, event, location, event_date)
