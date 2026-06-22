"""Pattern modules for Phase 2 general lead engine."""
from pipeline.patterns.base import PatternCandidate, PatternModule, PatternResult
from pipeline.patterns.entity_aggregation import EntityAggregationPattern
from pipeline.patterns.event_signal import EventSignalPattern
from pipeline.patterns.registry import get_pattern_module

__all__ = [
    "EntityAggregationPattern",
    "EventSignalPattern",
    "PatternCandidate",
    "PatternModule",
    "PatternResult",
    "get_pattern_module",
]
