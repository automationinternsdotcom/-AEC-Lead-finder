"""Guarded Pipedrive destination wrapper for Phase 2."""
from __future__ import annotations

from pathlib import Path

from pipeline.config import Settings
from pipeline.destinations.base import (
    DeliveryNotApproved,
    DeliveryPreview,
    DeliveryRecord,
)
from pipeline.spec import DestinationV2


class PipedriveDestination:
    destination_type = "pipedrive"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def validate_config(self, config: DestinationV2) -> None:
        if config.type != "pipedrive":
            raise ValueError("PipedriveDestination requires destination type 'pipedrive'")
        if not config.credential_ref:
            raise ValueError("pipedrive destination requires credential_ref")

    def preview(
        self,
        records: list[DeliveryRecord],
        *,
        output_dir: Path,
        run_id: str,
    ) -> DeliveryPreview:
        return DeliveryPreview(
            destination_type=self.destination_type,
            record_count=len(records),
            records=records,
        )

    def deliver(self, records: list[DeliveryRecord], *, approved: bool) -> DeliveryPreview:
        if not approved:
            raise DeliveryNotApproved("Pipedrive delivery requires explicit approval")
        # Live CRM delivery remains the existing pipeline.cli.push path until
        # Phase 2D gets operator-approved end-to-end testing.
        raise NotImplementedError(
            "Pipedrive live delivery is guarded; use the existing push CLI after preview approval"
        )
