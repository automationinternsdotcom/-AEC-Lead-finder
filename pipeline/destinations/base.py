"""Destination adapter contracts."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from pipeline.spec import DestinationV2


class DeliveryRecord(BaseModel):
    title: str
    company_name: str
    url: str | None = None
    priority: str | None = None
    score: int | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    notes: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class DeliveryPreview(BaseModel):
    destination_type: str
    record_count: int
    output_path: str | None = None
    records: list[DeliveryRecord] = Field(default_factory=list)


class DestinationAdapter(Protocol):
    destination_type: str

    def validate_config(self, config: DestinationV2) -> None:
        """Raise if the destination config is invalid."""

    def preview(
        self,
        records: list[DeliveryRecord],
        *,
        output_dir: Path,
        run_id: str,
    ) -> DeliveryPreview:
        """Create a human-reviewable preview."""

    def deliver(self, records: list[DeliveryRecord], *, approved: bool) -> DeliveryPreview:
        """Perform live delivery only when approved."""


class DeliveryNotApproved(RuntimeError):
    """Raised when live delivery is attempted without approval."""
