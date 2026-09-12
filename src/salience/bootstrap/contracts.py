from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ResearchEvidenceInput(BaseModel):
    source_uri: str
    fetched_at: datetime
    content: dict[str, Any]
    trust_level: str = "fixture"
    verification_status: str = "verified"


class StrategyVersionInput(BaseModel):
    strategy: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
