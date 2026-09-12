from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentManifest(BaseModel):
    """Immutable public identity and capability declaration for an agent version."""

    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tool_scopes: list[str]
    memory_scopes: list[str]
    effect_classification: str
    supports_sync: bool
    supports_async: bool
    timeout_seconds: int = Field(default=30, gt=0, le=3600)
    runtime_id: str | None = None
    protocol_compatibility: dict[str, str] = Field(default_factory=dict)
    trust_classification: str = "unclassified"
    delegated_authority_scopes: list[str] = Field(default_factory=list)


class AgentVersionView(AgentManifest):
    status: str = "enabled"
