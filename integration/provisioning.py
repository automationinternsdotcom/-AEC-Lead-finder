"""Idempotent provider setup plans and opt-in application."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .providers import PipedriveClient, WarmyClient

DEAL_FIELDS = (
    ("aether_lead_event_id", "Aether Lead Event ID", "varchar", None),
    ("aether_outreach_id", "Aether Outreach ID", "varchar", None),
    (
        "aether_contact_candidate_id",
        "Aether Source Contact Candidate ID",
        "varchar",
        None,
    ),
    ("warmy_prospect_id", "Warmy Prospect ID", "varchar", None),
    ("outreach_state", "Aether Outreach State", "varchar", None),
    (
        "reply_disposition",
        "Aether Reply Disposition",
        "enum",
        [
            {"label": "Pending review"},
            {"label": "Positive"},
            {"label": "Negative"},
            {"label": "Out of office"},
            {"label": "Unsubscribe"},
            {"label": "Other"},
        ],
    ),
    ("reply_received_at", "Aether Reply Received At", "varchar", None),
    ("unsubscribe_url", "Aether Unsubscribe URL", "varchar", None),
    ("article_url", "Aether Source Article URL", "varchar", None),
    ("date_posted", "Aether Source Date", "date", None),
)

PERSON_FIELDS = (
    ("aether_person_id", "Aether Person ID", "varchar", None),
    (
        "verification_status",
        "Aether Email Verification",
        "enum",
        [
            {"label": value}
            for value in ("Pending", "Valid", "Invalid", "Catch all", "Unknown")
        ],
    ),
    ("suppressed", "Aether Suppressed", "enum", [{"label": "Yes"}, {"label": "No"}]),
    ("suppression_reason", "Aether Suppression Reason", "varchar", None),
    ("unsubscribe_url", "Aether Unsubscribe URL", "varchar", None),
)

WARMY_EVENTS = [
    "reply.received",
    "email.bounced",
    "email.unsubscribed",
    "prospect.suppressed",
]


def plan(settings: Settings) -> dict[str, Any]:
    return {
        "pipedrive": {
            "pipeline_id": settings.pipedrive_pipeline_id,
            "stage_id": settings.pipedrive_stage_id,
            "owner_user_id": settings.pipedrive_jordan_user_id,
            "deal_fields": [item[1] for item in DEAL_FIELDS],
            "person_fields": [item[1] for item in PERSON_FIELDS],
            "webhook": f"{settings.public_base_url}/webhooks/pipedrive",
        },
        "warmy": {
            "webhook": f"{settings.public_base_url}/webhooks/warmy",
            "events": WARMY_EVENTS,
            "campaign_status": "draft",
        },
        "writes_enabled": settings.provider_writes_enabled,
    }


def apply(settings: Settings) -> dict[str, Any]:
    settings.require_provider_writes()
    pipedrive = PipedriveClient(settings)
    warmy = WarmyClient(settings)
    try:
        deal_fields, disposition_values = _ensure_fields(
            pipedrive, "dealFields", DEAL_FIELDS
        )
        person_fields, _ = _ensure_fields(pipedrive, "personFields", PERSON_FIELDS)
        pipedrive_url = f"{settings.public_base_url}/webhooks/pipedrive"
        warmy_url = f"{settings.public_base_url}/webhooks/warmy"
        pipedrive_webhook = next(
            (
                item
                for item in pipedrive.list_webhooks()
                if item.get("subscription_url") == pipedrive_url
            ),
            None,
        ) or pipedrive.create_webhook(
            pipedrive_url,
            name="Aether sales integration",
            event_action="change",
            event_object="lead",
        )
        warmy_webhook = next(
            (item for item in warmy.list_webhooks() if item.get("url") == warmy_url),
            None,
        ) or warmy.create_webhook(warmy_url, WARMY_EVENTS, "aether-warmy-webhook-v1")
        return {
            "PIPEDRIVE_DEAL_FIELDS": deal_fields,
            "PIPEDRIVE_PERSON_FIELDS": person_fields,
            "PIPEDRIVE_REPLY_DISPOSITION_VALUES": disposition_values,
            "pipedrive_webhook": pipedrive_webhook,
            "warmy_webhook": warmy_webhook,
        }
    finally:
        pipedrive.close()
        warmy.close()


def _ensure_fields(client, resource, definitions):
    existing = client.list_fields(resource)
    by_name = {
        str(item.get("field_name") or item.get("name")): item
        for item in existing
        if not item.get("is_deleted")
    }
    semantic_to_key: dict[str, str] = {}
    disposition_values: dict[str, str] = {}
    for semantic, label, field_type, options in definitions:
        field = by_name.get(label)
        if field is None:
            field = client.create_field(resource, label, field_type, options)
        key = str(field.get("field_code") or field.get("key") or "")
        if not key:
            raise RuntimeError(f"Pipedrive did not return a field code for {label}")
        semantic_to_key[semantic] = key
        if semantic == "reply_disposition":
            for option in field.get("options") or []:
                option_id = str(option.get("id") or "")
                option_label = (
                    str(option.get("label") or "").casefold().replace(" ", "_")
                )
                if option_id and option_label:
                    disposition_values[option_id] = option_label
    return semantic_to_key, disposition_values
