from __future__ import annotations

from typing import Protocol

from schema import LeadRecord, PipedriveSyncResult
from pipeline import util


class CrmClient(Protocol):
    def sync_approved_lead(self, lead: LeadRecord) -> PipedriveSyncResult:
        ...


class DryRunPipedriveMcpClient:
    """Safe default CRM client.

    Real MCP wiring should implement the same method and call the required
    Pipedrive MCP tools in the order described in the PRD.
    """

    def sync_approved_lead(self, lead: LeadRecord) -> PipedriveSyncResult:
        util.json_log(
            "crm_dry_run",
            lead_id=lead.lead_id,
            property_name=lead.property_name,
            note=_note_body(lead),
        )
        return PipedriveSyncResult(
            status="created",
            org_id=f"dry-org-{lead.lead_id}",
            person_id=f"dry-person-{lead.lead_id}" if _has_contact(lead) else None,
            lead_id=f"dry-lead-{lead.lead_id}",
            message="dry run only; no Pipedrive records were created",
        )


def _has_contact(lead: LeadRecord) -> bool:
    if lead.contact_name or lead.contact_email or lead.contact_phone:
        return True
    return bool(lead.enrichment and lead.enrichment.best_contact.has_contact)


def _note_body(lead: LeadRecord) -> str:
    enrichment = lead.enrichment
    best_contact = enrichment.best_contact if enrichment else None
    enriched_lines = []
    if enrichment:
        enriched_lines = [
            f"- Owner: {enrichment.owner_company or ''}",
            f"- Developer: {enrichment.developer_company or ''}",
            f"- Property manager: {enrichment.property_manager_company or ''}",
            f"- Best contact: {_format_best_contact(lead)}",
        ]

    return "\n".join(
        [
            "Aether Lead Intelligence",
            "",
            "Original article facts:",
            f"- Property: {lead.property_name or ''}",
            f"- Address: {lead.address or ''}",
            f"- Type: {lead.property_type or ''}",
            f"- Opening/status: {lead.opening_date_or_status or ''}",
            f"- Source: {lead.source_url}",
            "",
            "Enriched facts:",
            *(enriched_lines or ["- No enrichment available."]),
            "",
            "Why this matters for Aether:",
            "- Potential facility maintenance opportunity tied to a new or changing commercial asset.",
            "- Recommended next action: contact ownership or property management about Aether Assurance.",
            f"- Lead score: {lead.confidence_score}",
        ]
    )


def _format_best_contact(lead: LeadRecord) -> str:
    if lead.contact_name or lead.contact_email or lead.contact_phone:
        return " / ".join(
            item for item in [lead.contact_name, lead.contact_title, lead.contact_email, lead.contact_phone] if item
        )
    if not lead.enrichment:
        return ""
    contact = lead.enrichment.best_contact
    return " / ".join(item for item in [contact.name, contact.title, contact.email, contact.phone] if item)

