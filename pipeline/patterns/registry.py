"""Pattern module registry."""
from __future__ import annotations

from pipeline.patterns.base import PatternModule
from pipeline.patterns.entity_aggregation import EntityAggregationPattern
from pipeline.patterns.event_signal import EventSignalPattern
from pipeline.spec import LeadPatternType


_MODULES: dict[LeadPatternType, PatternModule] = {
    "event_signal": EventSignalPattern(),
    "entity_aggregation": EntityAggregationPattern(),
}


def get_pattern_module(pattern_type: LeadPatternType) -> PatternModule:
    try:
        return _MODULES[pattern_type]
    except KeyError as e:
        raise NotImplementedError(
            f"pattern module {pattern_type!r} is contract-supported but not implemented in Phase 2B"
        ) from e
