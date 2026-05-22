from datetime import date
from typing import Literal

from pydantic import BaseModel


class ExtractedArticle(BaseModel):
    title: str
    published_date: date | None
    summary_2sent: str
    signal_type: Literal[
        "opening", "development", "acquisition",
        "expansion", "lease", "construction", "other",
    ]
    company_name: str
    company_domain_guess: str | None
    property_type: Literal[
        "office", "industrial", "multifamily",
        "retail", "medical", "mixed", "other",
    ]
    address: str | None
    city: str | None
    square_footage: int | None
    dollar_value: int | None
    unit_count: int | None
    az_relevant: bool
    confidence: float
    # Jordan's protocol (see skill/aether_daily_routine.md Step 2b for the rules):
    #  high   — active lease-up, new occupancy, openings, expansions, mgmt changes
    #  medium — land acquisitions, industrial deals, generic commercial transactions
    #  low    — macro commentary, mortgage news, residential, rankings/awards,
    #           out-of-state, anything dropped by is_qualifying
    priority: Literal["high", "medium", "low"]
    # One sentence explaining the priority — populated for ALL articles (incl. low/dropped).
    # Flows into the Pipedrive note + downstream audit trail.
    filter_reason: str
    # Aether-voice reason to reach out: asset-preservation framing, not "cleaning".
    # Null on low-priority (those are dropped before push); required for high/medium.
    service_angle: str | None
