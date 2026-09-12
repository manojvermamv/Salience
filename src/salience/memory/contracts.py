from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class MemoryRecordInput(BaseModel):
    scope: Literal["working", "semantic", "evidence", "episodic", "analytics", "artifact"]
    content: dict[str, Any]
    trust_level: str = "unclassified"
    confidence: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    data_classification: str = "internal"
    retention_policy: str | None = None
    expires_at: datetime | None = None
