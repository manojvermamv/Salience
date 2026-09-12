from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolManifest:
    server_id: str
    tool_name: str
    input_schema: dict[str, Any]
    required_scope: str
    protocol_version: str
    timeout_seconds: float
    protocol_features: tuple[str, ...] = ()
    authentication_mode: str = "none"
    sdk_version: str | None = None


@dataclass(frozen=True)
class ToolResult:
    output: dict[str, Any]
    provenance: dict[str, Any]
    protocol_version: str


class McpToolServer(Protocol):
    server_id: str
    protocol_version: str

    async def discover(self, tool_name: str) -> ToolManifest: ...

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...
