from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from salience.observability.tracing import TraceContext


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    output_schema: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    capability: str = "unspecified"
    parent_agent_run_id: UUID | None = None
    job_id: UUID | None = None
    trace_context: TraceContext | None = None
    input_artifact_reference: str | None = None


@dataclass(frozen=True)
class ModelResult:
    runtime_id: str
    output: dict[str, Any]
    usage: dict[str, int]
    latency_ms: int
    provider_metadata: dict[str, str] = field(default_factory=dict)
    provider: str = "unknown"
    model: str | None = None
    provider_version: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    output_artifact_reference: str | None = None
    actual_cost_micros: int = 0


class ModelGateway(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResult: ...
