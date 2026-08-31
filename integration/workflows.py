"""Idempotent business workflows connecting Scout, Warmy, Gmail, and Pipedrive."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

from .config import ActivationBlocked, Settings
from .models import (
    ContactSync,
    MappingRecord,
    ReplyDisposition,
    SuppressionReason,
    VerificationStatus,
    WorkItem,
)
from .providers import GmailClient, GmailHistoryExpired, PipedriveClient, WarmyClient
from .security import issue_unsubscribe_token

LOG = logging.getLogger(__name__)


class WorkflowRetry(RuntimeError):
    """A normal incomplete provider operation that should be retried."""


class SalesWorkflows:
    def __init__(
        self,
        settings: Settings,
        db,
        *,
        warmy: WarmyClient | None = None,
        pipedrive: PipedriveClient | None = None,
        gmail: GmailClient | None = None,
    ):
        self.settings = settings
        self.db = db
        self._warmy = warmy
        self._pipedrive = pipedrive
        self._gmail = gmail
        self._operation_owner = uuid.uuid4().hex

    @property
    def warmy(self) -> WarmyClient:
        if self._warmy is None:
            self._warmy = WarmyClient(self.settings)
        return self._warmy

    @property
    def pipedrive(self) -> PipedriveClient:
        if self._pipedrive is None:
            self._pipedrive = PipedriveClient(self.settings)
        return self._pipedrive

    @property
    def gmail(self) -> GmailClient:
        if self._gmail is None:
            self._gmail = GmailClient(self.settings)
        return self._gmail

    def close(self) -> None:
        if self._warmy:
            self._warmy.close()
        if self._pipedrive:
            self._pipedrive.close()

    def handle(self, item: WorkItem) -> None:
        handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "scout.contact.sync": self.sync_contact,
            "warmy.verify": self.verify_contact,
            "warmy.enroll": self.enroll_contact,
            "warmy.event": self.handle_warmy_event,
            "pipedrive.event": self.handle_pipedrive_event,
            "pipedrive.convert": self.convert_lead,
            "suppression.sync": self.sync_suppression,
            "gmail.sync": self.sync_jordan_inbox,
        }
        try:
            handler = handlers[item.kind]
        except KeyError as error:
            raise ValueError(f"unknown work kind: {item.kind}") from error
        handler(item.payload)

    def _operation(
        self,
        provider: str,
        key: str,
        payload: dict[str, Any],
        call: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        existing = self.db.get_operation(provider, key)
        if existing and existing.get("status") == "completed":
            return existing.get("response_payload") or existing.get("response") or {}
        if not self.db.claim_operation(
            provider,
            key,
            payload,
            owner=self._operation_owner,
            lease_seconds=self.settings.worker_lease_seconds,
        ):
            raise WorkflowRetry(f"{provider} operation is already in progress: {key}")
        try:
            response = call()
        except Exception as error:
            self.db.fail_operation(provider, key, error)
            raise
        external_id = _external_id(response)
        self.db.complete_operation(provider, key, response, external_id=external_id)
        return response

    def sync_contact(self, payload: dict[str, Any]) -> None:
        contact = ContactSync.model_validate(payload)
        mapping = self.db.get_mapping(outreach_id=contact.outreach_id)
        if mapping is None:
            mapping = MappingRecord(
                outreach_id=contact.outreach_id,
                source_contact_candidate_id=contact.source_contact_candidate_id,
                source_verification_status=contact.source_verification_status,
                source_verification_reason=contact.source_verification_reason,
                source_provider=contact.source_provider,
                email=contact.email,
                lead_event_id=contact.lead_event_id,
                organization_id=contact.organization_id,
                person_id=contact.person_id,
            )
            self.db.upsert_mapping(mapping)
        else:
            if mapping.email != contact.email:
                self.apply_suppression(
                    mapping.email,
                    SuppressionReason.MANUAL,
                    "scout_contact_revision",
                    mapping=mapping,
                )
                self.db.update_mapping(
                    contact.outreach_id,
                    email=contact.email,
                    pipedrive_person_id=None,
                    warmy_prospect_id=None,
                    warmy_campaign_id=None,
                    warmy_mailbox_id=None,
                    gmail_thread_id=None,
                    verification_status=VerificationStatus.PENDING,
                    reply_disposition=None,
                    reply_received_at=None,
                )
            self.db.update_mapping(
                contact.outreach_id,
                source_contact_candidate_id=contact.source_contact_candidate_id,
                source_verification_status=contact.source_verification_status,
                source_verification_reason=contact.source_verification_reason,
                source_provider=contact.source_provider,
            )
        mapping = self._mapping(contact.outreach_id)

        token, token_id = issue_unsubscribe_token(
            self.settings.unsubscribe_secret, contact.email
        )
        self.db.store_unsubscribe_token(token_id, contact.email)
        unsubscribe_url = f"{self.settings.public_base_url}/unsubscribe?t={token}"

        self.settings.require_provider_writes()
        organization_id = mapping.pipedrive_organization_id
        if organization_id is None:
            organization_id = self.pipedrive.find_organization(
                contact.organization_name
            )
            if organization_id is None:
                response = self._operation(
                    "pipedrive",
                    f"organization:create:{contact.organization_id}",
                    contact.model_dump(mode="json"),
                    lambda: {
                        "id": self.pipedrive.create_organization(
                            contact.organization_name,
                            self.settings.pipedrive_jordan_user_id,
                            contact.location,
                        )
                    },
                )
                organization_id = int(response["id"])
            self.db.update_mapping(
                contact.outreach_id,
                pipedrive_organization_id=organization_id,
            )

        person_id = mapping.pipedrive_person_id
        person_fields = self._person_fields(
            aether_person_id=contact.person_id,
            verification_status=VerificationStatus.PENDING.value,
            suppressed="yes" if self.db.is_suppressed(contact.email) else "no",
            suppression_reason=""
            if not self.db.is_suppressed(contact.email)
            else SuppressionReason.MANUAL.value,
            unsubscribe_url=unsubscribe_url,
        )
        if person_id is None:
            person_id = self.pipedrive.find_person(contact.email)
            if person_id is None:
                response = self._operation(
                    "pipedrive",
                    f"person:create:{contact.person_id}:{contact.email}",
                    contact.model_dump(mode="json"),
                    lambda: {
                        "id": self.pipedrive.create_person(
                            contact.person_name,
                            contact.email,
                            organization_id,
                            self.settings.pipedrive_jordan_user_id,
                            person_fields,
                        )
                    },
                )
                person_id = int(response["id"])
            self.db.update_mapping(contact.outreach_id, pipedrive_person_id=person_id)
        if person_fields:
            self.pipedrive.update_person(person_id, person_fields)

        lead_id = mapping.pipedrive_lead_id
        lead_fields = self._deal_fields(
            aether_lead_event_id=contact.lead_event_id,
            aether_outreach_id=contact.outreach_id,
            aether_contact_candidate_id=contact.source_contact_candidate_id,
            outreach_state="suppressed"
            if self.db.is_suppressed(contact.email)
            else "created",
            unsubscribe_url=unsubscribe_url,
            article_url=contact.article_url,
            date_posted=contact.date_posted,
        )
        if lead_id is None:
            lead_id = self.pipedrive.find_lead_by_outreach_id(contact.outreach_id)
            if lead_id is None:
                response = self._operation(
                    "pipedrive",
                    f"lead:create:{contact.outreach_id}",
                    contact.model_dump(mode="json"),
                    lambda: {
                        "id": self.pipedrive.create_lead(
                            _lead_title(contact),
                            person_id,
                            organization_id,
                            self.settings.pipedrive_jordan_user_id,
                            lead_fields,
                        )
                    },
                )
                lead_id = str(response["id"])
            self.db.update_mapping(contact.outreach_id, pipedrive_lead_id=lead_id)
        self.pipedrive.update_lead(
            lead_id,
            {
                "person_id": person_id,
                "organization_id": organization_id,
                **lead_fields,
            },
        )
        if mapping.pipedrive_deal_id:
            self.pipedrive.update_deal(
                mapping.pipedrive_deal_id,
                {"person_id": person_id, "custom_fields": lead_fields},
            )

        if self.db.is_suppressed(contact.email):
            return

        mapping = self.db.get_mapping(outreach_id=contact.outreach_id)
        if not mapping.warmy_prospect_id:
            reusable = next(
                (
                    item
                    for item in self.db.get_mappings_by_email(contact.email)
                    if item.warmy_prospect_id
                ),
                None,
            )
            if reusable:
                self.db.update_mapping(
                    contact.outreach_id,
                    warmy_prospect_id=reusable.warmy_prospect_id,
                )
                mapping = self.db.get_mapping(outreach_id=contact.outreach_id)
        if not mapping.warmy_prospect_id:
            first_name, last_name = _split_name(contact.person_name)
            warmy_payload = contact.model_dump(mode="json") | {
                "first_name": first_name,
                "last_name": last_name,
                "unsubscribe_url": unsubscribe_url,
            }
            response = self._operation(
                "warmy",
                f"prospect:create:{contact.email}",
                warmy_payload,
                lambda: self.warmy.create_prospect(
                    warmy_payload,
                    f"aether-prospect-{contact.email}",
                ),
            )
            prospect = response.get("data") or response
            prospect_id = str(prospect["id"])
            self.db.update_mapping(
                contact.outreach_id,
                warmy_prospect_id=prospect_id,
            )
        mapping = self._mapping(contact.outreach_id)
        fields = self._deal_fields(warmy_prospect_id=mapping.warmy_prospect_id)
        if fields:
            self.pipedrive.update_lead(lead_id, fields)

        self.db.enqueue_work(
            "warmy.verify",
            f"warmy:verify:{contact.outreach_id}:{contact.email}",
            {
                "outreach_id": contact.outreach_id,
                "email": contact.email,
            },
        )

    def verify_contact(self, payload: dict[str, Any]) -> None:
        outreach_id = str(payload["outreach_id"])
        email = str(payload["email"]).strip().casefold()
        mapping = self._mapping(outreach_id)
        if mapping.email != email:
            return
        response = self._operation(
            "warmy",
            f"verify:{mapping.email}",
            payload,
            lambda: self.warmy.verify_email(email, f"aether-verify-{mapping.email}"),
        )
        data = response.get("data") or response
        status = _verification_status(data)
        self.db.update_mapping(outreach_id, verification_status=status)
        fields = self._person_fields(verification_status=status.value)
        if mapping.pipedrive_person_id and fields:
            self.pipedrive.update_person(mapping.pipedrive_person_id, fields)

        if status == VerificationStatus.VALID:
            self.db.enqueue_work(
                "warmy.enroll",
                f"warmy:enroll:{self.settings.warmy_campaign_id}:{mapping.warmy_prospect_id}",
                {"outreach_id": outreach_id},
            )
        elif status == VerificationStatus.INVALID:
            self.apply_suppression(
                email,
                SuppressionReason.INVALID,
                "warmy_verification",
                mapping=mapping,
            )

    def enroll_contact(self, payload: dict[str, Any]) -> None:
        outreach_id = str(payload["outreach_id"])
        mapping = self._mapping(outreach_id)
        if mapping.verification_status != VerificationStatus.VALID:
            return
        if self.db.is_suppressed(mapping.email):
            return
        self.settings.require_campaign_activation()
        if not mapping.warmy_prospect_id:
            raise WorkflowRetry("Warmy prospect has not been created")
        campaign = self.warmy.get_campaign(self.settings.warmy_campaign_id)
        self._validate_live_campaign(campaign)
        route_key = f"warmy-route:{mapping.warmy_prospect_id}"
        route = self.db.get_state(route_key) or {}
        if route.get("outreach_id") not in (None, "", outreach_id):
            if mapping.pipedrive_lead_id:
                fields = self._deal_fields(outreach_state="duplicate_email")
                if fields:
                    self.pipedrive.update_lead(mapping.pipedrive_lead_id, fields)
            return
        self._operation(
            "warmy",
            f"enroll:{self.settings.warmy_campaign_id}:{mapping.warmy_prospect_id}",
            payload,
            lambda: self.warmy.enroll(
                self.settings.warmy_campaign_id,
                [mapping.warmy_prospect_id],
                f"aether-enroll-{self.settings.warmy_campaign_id}-{mapping.warmy_prospect_id}",
            ),
        )
        self.db.update_mapping(
            outreach_id,
            warmy_campaign_id=self.settings.warmy_campaign_id,
        )
        self.db.set_state(
            route_key,
            {
                "outreach_id": outreach_id,
                "campaign_id": self.settings.warmy_campaign_id,
            },
        )
        if mapping.pipedrive_lead_id:
            fields = self._deal_fields(outreach_state="enrolled")
            if fields:
                self.pipedrive.update_lead(mapping.pipedrive_lead_id, fields)

    def handle_warmy_event(self, payload: dict[str, Any]) -> None:
        event_type = str(
            payload.get("event_type")
            or payload.get("type")
            or payload.get("event")
            or ""
        )
        data = payload.get("data") or {}
        event_id = str(payload.get("event_id") or "")
        email = str(data.get("prospectEmail") or data.get("email") or "").casefold()
        prospect_id = str(data.get("prospectId") or "")
        mapping = None
        if prospect_id:
            route = self.db.get_state(f"warmy-route:{prospect_id}") or {}
            if route.get("outreach_id"):
                mapping = self.db.get_mapping(outreach_id=route["outreach_id"])
            if mapping is None:
                mapping = self.db.get_mapping(warmy_prospect_id=prospect_id)
        elif email:
            candidates = self.db.get_mappings_by_email(email)
            mapping = next(
                (item for item in candidates if item.warmy_campaign_id),
                candidates[0] if candidates else None,
            )

        if event_type == "email.bounced":
            if email:
                self.apply_suppression(
                    email,
                    SuppressionReason.BOUNCE,
                    "warmy",
                    external_event_id=event_id,
                    mapping=mapping,
                )
            return
        if event_type in {"email.unsubscribed", "prospect.suppressed"}:
            if email:
                self.apply_suppression(
                    email,
                    SuppressionReason.UNSUBSCRIBE,
                    "warmy",
                    external_event_id=event_id,
                    mapping=mapping,
                )
            return
        if event_type != "reply.received":
            return
        if mapping is None:
            raise WorkflowRetry(f"no mapping for Warmy reply {prospect_id or email}")

        received_at = _parse_datetime(data.get("receivedAt"))
        self.db.update_mapping(
            mapping.outreach_id,
            reply_disposition=ReplyDisposition.PENDING_REVIEW,
            reply_received_at=received_at,
            warmy_mailbox_id=str(data.get("mailboxId") or "") or None,
        )
        mailbox_id = str(data.get("mailboxId") or "")
        mailbox = self.settings.warmy_mailbox_emails.get(mailbox_id, "")
        message_ref = str(data.get("messageId") or "")
        if not mailbox:
            raise WorkflowRetry(
                f"no Gmail mailbox mapping for Warmy mailbox {mailbox_id}"
            )
        gmail_message_id = self.gmail.find_message(mailbox, message_ref)
        if not gmail_message_id:
            raise WorkflowRetry(f"Gmail message not indexed yet: {message_ref}")
        self._operation(
            "gmail",
            f"forward:{event_id or gmail_message_id}",
            {"mailbox": mailbox, "message_id": gmail_message_id},
            lambda: self.gmail.forward_message(
                mailbox, gmail_message_id, self.settings.gmail_forward_to
            ),
        )
        message = self.gmail.get_message(mailbox, gmail_message_id, format="metadata")
        thread_id = str(message.get("threadId") or "")
        if thread_id:
            self.db.update_mapping(mapping.outreach_id, gmail_thread_id=thread_id)
        if mapping.pipedrive_lead_id:
            self._operation(
                "pipedrive",
                f"review-activity:{event_id or gmail_message_id}",
                payload,
                lambda: {
                    "id": self.pipedrive.add_lead_activity(
                        mapping.pipedrive_lead_id,
                        "Review Warmy reply",
                        self.settings.pipedrive_jordan_user_id,
                        note=(
                            f"Reply received from {mapping.email}. "
                            f"Subject: {data.get('subject') or '(no subject)'}. "
                            "The full message was forwarded to Jordan."
                        ),
                    )
                },
            )
            fields = self._deal_fields(
                reply_disposition=ReplyDisposition.PENDING_REVIEW.value,
                reply_received_at=received_at.isoformat(),
                outreach_state="reply_review",
            )
            if fields:
                self.pipedrive.update_lead(mapping.pipedrive_lead_id, fields)

    def handle_pipedrive_event(self, payload: dict[str, Any]) -> None:
        meta = payload.get("meta") or {}
        current = payload.get("current") or payload.get("data") or {}
        entity = str(
            meta.get("entity") or meta.get("object") or current.get("object") or ""
        )
        if entity not in {"lead", "leads"}:
            return
        lead_id = str(
            meta.get("entity_id") or meta.get("id") or current.get("id") or ""
        )
        mapping = self.db.get_mapping(pipedrive_lead_id=lead_id)
        if mapping is None:
            return
        field_key = self.settings.pipedrive_deal_fields.get("reply_disposition", "")
        custom_fields = current.get("custom_fields") or {}
        raw_disposition = (
            custom_fields.get(field_key)
            if field_key
            else current.get("reply_disposition")
        )
        disposition = self._disposition(raw_disposition)
        if disposition is None:
            return
        self.db.update_mapping(mapping.outreach_id, reply_disposition=disposition)
        if disposition == ReplyDisposition.POSITIVE:
            self.db.enqueue_work(
                "pipedrive.convert",
                f"pipedrive:convert:{mapping.pipedrive_lead_id}",
                {"outreach_id": mapping.outreach_id},
            )
        elif disposition in {ReplyDisposition.NEGATIVE, ReplyDisposition.UNSUBSCRIBE}:
            reason = (
                SuppressionReason.NEGATIVE_REPLY
                if disposition == ReplyDisposition.NEGATIVE
                else SuppressionReason.UNSUBSCRIBE
            )
            self.apply_suppression(mapping.email, reason, "pipedrive", mapping=mapping)
            if mapping.pipedrive_lead_id:
                self.pipedrive.archive_lead(mapping.pipedrive_lead_id)

    def convert_lead(self, payload: dict[str, Any]) -> None:
        if not self.settings.pipedrive_automation_ready:
            raise ActivationBlocked(
                "PIPEDRIVE_AUTOMATION_READY is disabled; conversion would miss follow-ups"
            )
        mapping = self._mapping(str(payload["outreach_id"]))
        if mapping.pipedrive_deal_id:
            return
        if not mapping.pipedrive_lead_id:
            raise WorkflowRetry("Pipedrive Lead has not been created")
        state_key = f"conversion:{mapping.pipedrive_lead_id}"
        state = self.db.get_state(state_key) or {}
        conversion_id = state.get("conversion_id")
        if not conversion_id:
            response = self._operation(
                "pipedrive",
                f"convert:{mapping.pipedrive_lead_id}",
                payload,
                lambda: {
                    "conversion_id": self.pipedrive.convert_lead(
                        mapping.pipedrive_lead_id,
                        self.settings.pipedrive_stage_id,
                    )
                },
            )
            conversion_id = response["conversion_id"]
            self.db.set_state(state_key, {"conversion_id": conversion_id})
        status = self.pipedrive.conversion_status(
            mapping.pipedrive_lead_id, conversion_id
        )
        if status.get("status") in {"not_started", "running"}:
            raise WorkflowRetry("Pipedrive Lead conversion is still running")
        if status.get("status") != "completed":
            raise RuntimeError(f"Pipedrive Lead conversion failed: {status}")
        deal_id = int(status.get("deal_id") or status.get("dealId"))
        self.pipedrive.update_deal(
            deal_id,
            {
                "owner_id": self.settings.pipedrive_jordan_user_id,
                "stage_id": self.settings.pipedrive_stage_id,
                "custom_fields": self._deal_fields(outreach_state="qualified_reply"),
            },
        )
        self.db.update_mapping(mapping.outreach_id, pipedrive_deal_id=deal_id)

    def apply_suppression(
        self,
        email: str,
        reason: SuppressionReason,
        source: str,
        *,
        external_event_id: str = "",
        mapping: MappingRecord | None = None,
    ) -> None:
        self.db.suppress(
            email,
            reason,
            source,
            external_event_id=external_event_id,
        )
        self.db.enqueue_work(
            "suppression.sync",
            f"suppression:{email.casefold()}:{reason.value}",
            {
                "email": email,
                "reason": reason.value,
                "source": source,
                "campaign_ids": [mapping.warmy_campaign_id]
                if mapping and mapping.warmy_campaign_id
                else [],
                "person_ids": [mapping.pipedrive_person_id]
                if mapping and mapping.pipedrive_person_id
                else [],
                "lead_ids": [mapping.pipedrive_lead_id]
                if mapping and mapping.pipedrive_lead_id
                else [],
                "deal_ids": [mapping.pipedrive_deal_id]
                if mapping and mapping.pipedrive_deal_id
                else [],
            },
        )
        if mapping and reason in {SuppressionReason.BOUNCE, SuppressionReason.INVALID}:
            self.db.update_mapping(
                mapping.outreach_id,
                verification_status=VerificationStatus.INVALID,
            )

    def sync_suppression(self, payload: dict[str, Any]) -> None:
        email = str(payload["email"]).casefold()
        reason = str(payload["reason"])
        mappings = self.db.get_mappings_by_email(email)
        self._operation(
            "warmy",
            f"suppress:{email}",
            payload,
            lambda: self.warmy.suppress([email], reason, f"aether-suppress-{email}"),
        )
        campaign_ids = set(payload.get("campaign_ids") or []) | {
            mapping.warmy_campaign_id
            for mapping in mappings
            if mapping.warmy_campaign_id
        }
        for campaign_id in campaign_ids:
            self._operation(
                "warmy",
                f"unenroll:{campaign_id}:{email}",
                payload,
                lambda campaign_id=campaign_id: self.warmy.unenroll(
                    campaign_id,
                    [email],
                    f"aether-unenroll-{campaign_id}-{email}",
                ),
            )
        person_ids = set(payload.get("person_ids") or []) | {
            mapping.pipedrive_person_id
            for mapping in mappings
            if mapping.pipedrive_person_id
        }
        for person_id in person_ids:
            fields = self._person_fields(suppressed="yes", suppression_reason=reason)
            if fields:
                self.pipedrive.update_person(person_id, fields)
        lead_ids = set(payload.get("lead_ids") or []) | {
            mapping.pipedrive_lead_id
            for mapping in mappings
            if mapping.pipedrive_lead_id
        }
        for lead_id in lead_ids:
            fields = self._deal_fields(outreach_state="suppressed")
            if fields:
                self.pipedrive.update_lead(lead_id, fields)
        deal_ids = set(payload.get("deal_ids") or []) | {
            mapping.pipedrive_deal_id
            for mapping in mappings
            if mapping.pipedrive_deal_id
        }
        for deal_id in deal_ids:
            fields = self._deal_fields(outreach_state="suppressed")
            if fields:
                self.pipedrive.update_deal(
                    deal_id,
                    {"custom_fields": fields},
                )

    def sync_jordan_inbox(self, payload: dict[str, Any]) -> None:
        mailbox = self.settings.gmail_forward_to
        state_key = f"gmail-history:{mailbox}"
        state = self.db.get_state(state_key)
        if not state:
            profile = self.gmail.profile(mailbox)
            self.db.set_state(state_key, {"history_id": str(profile["historyId"])})
            return
        try:
            response = self.gmail.history(mailbox, str(state["history_id"]))
            message_ids = [
                str((added.get("message") or {}).get("id") or "")
                for event in response.get("history") or []
                for added in event.get("messagesAdded") or []
            ]
            latest = str(response.get("historyId") or state["history_id"])
        except GmailHistoryExpired:
            message_ids = self.gmail.recent_inbox_messages(
                mailbox, newer_than_days=7, limit=500
            )
            latest = str(self.gmail.profile(mailbox)["historyId"])
        for message_id in dict.fromkeys(filter(None, message_ids)):
            self._sync_jordan_message(mailbox, message_id)
        self.db.set_state(state_key, {"history_id": latest})

    def unsubscribe(self, email: str) -> None:
        mapping = self.db.get_mapping(email=email)
        self.apply_suppression(
            email,
            SuppressionReason.UNSUBSCRIBE,
            "unsubscribe_link",
            mapping=mapping,
        )

    def _mapping(self, outreach_id: str) -> MappingRecord:
        mapping = self.db.get_mapping(outreach_id=outreach_id)
        if mapping is None:
            raise WorkflowRetry(f"mapping not found: {outreach_id}")
        return mapping

    def _sync_jordan_message(self, mailbox: str, message_id: str) -> None:
        message = self.gmail.get_message(mailbox, message_id, format="metadata")
        headers = {
            item["name"].casefold(): item["value"]
            for item in (message.get("payload") or {}).get("headers") or []
        }
        sender = parseaddr(headers.get("from", ""))[1].casefold()
        mapping = self.db.get_mapping(email=sender) if sender else None
        if not mapping or not mapping.pipedrive_deal_id:
            return
        self.db.update_mapping(
            mapping.outreach_id,
            reply_received_at=datetime.now(UTC),
            gmail_thread_id=str(message.get("threadId") or "") or None,
        )
        fields = self._deal_fields(outreach_state="replied")
        if fields:
            self.pipedrive.update_deal(
                mapping.pipedrive_deal_id,
                {"custom_fields": fields},
            )

    def _validate_live_campaign(self, response: dict[str, Any]) -> None:
        campaign = response.get("data") if isinstance(response, dict) else None
        if not isinstance(campaign, dict):
            campaign = response
        errors: list[str] = []
        if str(campaign.get("id") or "") != self.settings.warmy_campaign_id:
            errors.append("campaign ID mismatch")
        status = str(campaign.get("status") or "").casefold()
        if status not in {"draft", "paused", "scheduled", "running"}:
            errors.append(f"unsafe campaign status {status or '(missing)'}")
        mailbox_values = campaign.get("mailboxIds") or campaign.get("mailboxes") or []
        mailbox_ids = {
            str(item.get("id") if isinstance(item, dict) else item)
            for item in mailbox_values
        }
        if mailbox_ids != set(self.settings.warmy_mailbox_ids):
            errors.append("mailbox set mismatch")
        if int(campaign.get("dailySendLimit") or 0) != self.settings.warmy_daily_limit:
            errors.append("daily send limit mismatch")
        if len(campaign.get("steps") or []) != 4:
            errors.append("campaign must contain four steps")
        for name in ("stopOnReply", "stopOnBounce", "stopOnUnsubscribe"):
            if campaign.get(name) is not True:
                errors.append(f"{name} must be enabled")
        if errors:
            raise ActivationBlocked("live Warmy campaign blocked: " + ", ".join(errors))

    def _deal_fields(self, **values: Any) -> dict[str, Any]:
        return {
            field_key: values[semantic]
            for semantic, field_key in self.settings.pipedrive_deal_fields.items()
            if semantic in values and field_key and values[semantic] not in (None, "")
        }

    def _person_fields(self, **values: Any) -> dict[str, Any]:
        return {
            field_key: str(values[semantic])
            for semantic, field_key in self.settings.pipedrive_person_fields.items()
            if semantic in values and field_key and values[semantic] not in (None, "")
        }

    def _disposition(self, value: Any) -> ReplyDisposition | None:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get("id", value.get("value", value.get("label")))
        if value is None:
            return None
        normalized = (
            self.settings.pipedrive_reply_disposition_values.get(str(value), str(value))
            .casefold()
            .replace(" ", "_")
        )
        try:
            return ReplyDisposition(normalized)
        except ValueError:
            return None


def _external_id(response: dict[str, Any]) -> str:
    data = response.get("data") if isinstance(response, dict) else None
    target = data if isinstance(data, dict) else response
    return str(target.get("id") or target.get("conversion_id") or "")


def _split_name(name: str) -> tuple[str, str]:
    first, separator, last = name.strip().partition(" ")
    return first, last if separator else ""


def _lead_title(contact: ContactSync) -> str:
    subject = contact.event or contact.organization_name
    return f"{contact.organization_name} — {contact.person_name} — {subject}"[:255]


def _verification_status(data: dict[str, Any]) -> VerificationStatus:
    status = str(data.get("status") or "unknown").casefold().replace("-", "_")
    if data.get("is_catch_all") or status in {"catch_all", "catchall"}:
        return VerificationStatus.CATCH_ALL
    if status in {"valid", "deliverable", "verified"}:
        return VerificationStatus.VALID
    if status in {"invalid", "undeliverable", "rejected"}:
        return VerificationStatus.INVALID
    return VerificationStatus.UNKNOWN


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(str(value))
    return datetime.now(UTC)
