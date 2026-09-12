import asyncio
from typing import Any

from jsonschema import ValidationError, validate

from salience.mcp.contracts import McpToolServer, ToolManifest, ToolResult


class ToolPermissionDenied(PermissionError):
    pass


class ToolProtocolIncompatible(ValueError):
    pass


class ToolGateway:
    def __init__(
        self,
        *,
        server: McpToolServer,
        allowed_scopes: frozenset[str],
        supported_protocol_version: str,
    ) -> None:
        self._server = server
        self._allowed_scopes = allowed_scopes
        self._supported_protocol_version = supported_protocol_version

    async def discover(self, server_id: str, tool_name: str) -> ToolManifest:
        if server_id != self._server.server_id:
            raise LookupError(server_id)
        if self._server.protocol_version != self._supported_protocol_version:
            raise ToolProtocolIncompatible(self._server.protocol_version)
        return await self._server.discover(tool_name)

    async def invoke(self, manifest: ToolManifest, arguments: dict[str, Any]) -> ToolResult:
        if manifest.required_scope not in self._allowed_scopes:
            raise ToolPermissionDenied(manifest.required_scope)
        if manifest.protocol_version != self._supported_protocol_version:
            raise ToolProtocolIncompatible(manifest.protocol_version)
        try:
            validate(arguments, manifest.input_schema)
        except ValidationError as error:
            raise ValueError(f"tool input schema validation failed: {error.message}") from error
        output = await asyncio.wait_for(
            self._server.invoke(manifest.tool_name, arguments),
            timeout=manifest.timeout_seconds,
        )
        return ToolResult(
            output=output,
            provenance={"server_id": manifest.server_id, "tool_name": manifest.tool_name},
            protocol_version=manifest.protocol_version,
        )
