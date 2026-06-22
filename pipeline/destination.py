"""Destination selection from CampaignSpec.

Phase 1 keeps the existing Pipedrive writer, but reads the campaign
destination before pushing so delivery is no longer an implicit hardcode.
Future destinations should branch from this boundary rather than from
pipeline.cli.push.
"""
from __future__ import annotations

from pipeline.spec import CampaignSpec


class DestinationError(RuntimeError):
    """Raised when a campaign requests a destination this CLI cannot serve."""


def require_pipedrive_destination(spec: CampaignSpec) -> None:
    """Fail early unless this campaign is configured for Pipedrive delivery."""
    if spec.destination.type != "pipedrive":
        raise DestinationError(
            f"campaign {spec.campaign_id!r} destination is "
            f"{spec.destination.type!r}; pipeline.cli.push currently supports "
            "only 'pipedrive'"
        )
    if spec.destination.pipedrive is None:
        raise DestinationError(
            f"campaign {spec.campaign_id!r} destination is 'pipedrive' "
            "but no pipedrive config block is present"
        )
