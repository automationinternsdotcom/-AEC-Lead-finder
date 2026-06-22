"""Destination adapter registry."""
from __future__ import annotations

from pipeline.destinations.base import DestinationAdapter
from pipeline.destinations.excel import ExcelDestination
from pipeline.destinations.pipedrive import PipedriveDestination
from pipeline.spec import DestinationV2


def destination_for(config: DestinationV2) -> DestinationAdapter:
    if config.type == "excel":
        return ExcelDestination()
    if config.type == "pipedrive":
        return PipedriveDestination()
    raise NotImplementedError(f"destination {config.type!r} is contract-supported but not implemented")
