"""Validated Warmy campaign manifest with immutable safety defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import ActivationBlocked, Settings

COPY_PLACEHOLDER = "TODO_APPROVED_COPY"


class CampaignStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stepIndex: int = Field(ge=0)
    type: str = "email"
    subject: str = Field(min_length=1)
    bodyHtml: str = Field(min_length=1)
    bodyText: str = Field(min_length=1)
    delayDays: int = Field(ge=0)
    delayHours: int = Field(default=0, ge=0, le=23)
    isActive: bool = True

    @field_validator("type")
    @classmethod
    def email_only(cls, value: str) -> str:
        if value != "email":
            raise ValueError("Aether's initial campaign must be email-only")
        return value


class CampaignManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = "Aether AEC evergreen outreach"
    channel: str = "email"
    timezone: str = "America/Phoenix"
    dailySendLimit: int = Field(default=150, ge=1, le=300)
    sendingWindowStart: int = Field(default=8, ge=0, le=23)
    sendingWindowEnd: int = Field(default=16, ge=0, le=23)
    scheduleDays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    stopOnReply: bool = True
    stopOnBounce: bool = True
    stopOnUnsubscribe: bool = True
    trackOpens: bool = False
    trackClicks: bool = False
    mailboxIds: list[str]
    steps: list[CampaignStep] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def enforce_initial_policy(self):
        if self.channel != "email":
            raise ValueError("campaign channel must be email")
        if self.timezone != "America/Phoenix":
            raise ValueError("campaign timezone must be America/Phoenix")
        if self.scheduleDays != [1, 2, 3, 4, 5]:
            raise ValueError("campaign schedule must be Monday through Friday")
        if (self.sendingWindowStart, self.sendingWindowEnd) != (8, 16):
            raise ValueError("campaign sending window must be 08:00–16:00")
        if not all((self.stopOnReply, self.stopOnBounce, self.stopOnUnsubscribe)):
            raise ValueError("reply, bounce, and unsubscribe stops are mandatory")
        if [step.delayDays for step in self.steps] != [0, 3, 7, 14]:
            raise ValueError("campaign delays must be day 0, 3, 7, and 14")
        if [step.stepIndex for step in self.steps] != [0, 1, 2, 3]:
            raise ValueError("campaign step indexes must be 0 through 3")
        return self


def load_campaign(path: str | Path, settings: Settings) -> CampaignManifest:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw["mailboxIds"] = list(settings.warmy_mailbox_ids)
    raw["dailySendLimit"] = settings.warmy_daily_limit
    serialized = yaml.safe_dump(raw)
    if COPY_PLACEHOLDER in serialized:
        raise ActivationBlocked("campaign copy is still a TODO placeholder")
    if not settings.email_templates_approved:
        raise ActivationBlocked("EMAIL_TEMPLATES_APPROVED is not enabled")
    if not settings.postal_address:
        raise ActivationBlocked("AETHER_POSTAL_ADDRESS is required")
    for index, step in enumerate(raw.get("steps") or []):
        if "{{AETHER_POSTAL_ADDRESS}}" not in str(
            step.get("bodyHtml") or ""
        ) or "{{AETHER_POSTAL_ADDRESS}}" not in str(step.get("bodyText") or ""):
            raise ActivationBlocked(
                f"step {index} is missing the postal-address placeholder"
            )
    raw = _replace(raw, "{{AETHER_POSTAL_ADDRESS}}", settings.postal_address)
    manifest = CampaignManifest.model_validate(raw)
    if len(manifest.mailboxIds) != 6:
        raise ActivationBlocked(
            "WARMY_MAILBOX_IDS must contain all six Aether mailboxes"
        )
    for step in manifest.steps:
        if (
            "{{unsubscribeUrl}}" not in step.bodyHtml
            or "{{unsubscribeUrl}}" not in step.bodyText
        ):
            raise ActivationBlocked(
                f"step {step.stepIndex} is missing the unsubscribe link"
            )
    return manifest


def _replace(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, old, new) for key, item in value.items()}
    return value
