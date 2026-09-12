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
    source_uri: str | None = None
    verification_status: str = "unverified"
    writer_identity: str | None = None
    data_classification: str = "internal"
    retention_policy: str | None = None
    expires_at: datetime | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    span_id: str | None = None
    verified_at: datetime | None = None
    valid_until: datetime | None = None
    source_identity: str | None = None
    effect_classification: str | None = None
    tool_scope: list[str] = Field(default_factory=list)
    network_scope: list[str] = Field(default_factory=list)
    memory_write_authority: list[str] = Field(default_factory=list)
    writer_actor: dict[str, Any] = Field(default_factory=dict)
