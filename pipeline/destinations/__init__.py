"""Destination adapters for Phase 2 preview and delivery."""
from pipeline.destinations.base import DeliveryPreview, DeliveryRecord, DestinationAdapter
from pipeline.destinations.excel import ExcelDestination
from pipeline.destinations.pipedrive import PipedriveDestination
from pipeline.destinations.registry import destination_for

__all__ = [
    "DeliveryPreview",
    "DeliveryRecord",
    "DestinationAdapter",
    "ExcelDestination",
    "PipedriveDestination",
    "destination_for",
]
