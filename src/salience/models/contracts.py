from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    output_schema: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResult:
    runtime_id: str
    output: dict[str, Any]
    usage: dict[str, int]
    latency_ms: int
    provider_metadata: dict[str, str] = field(default_factory=dict)


class ModelGateway(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResult: ...
