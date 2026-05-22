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
