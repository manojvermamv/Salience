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
        legacy_protocol_versions: frozenset[str] = frozenset(),
    ) -> None:
        self._server = server
        self._allowed_scopes = allowed_scopes
        self._supported_protocol_version = supported_protocol_version
        self._legacy_protocol_versions = legacy_protocol_versions

    async def discover(self, server_id: str, tool_name: str) -> ToolManifest:
        if server_id != self._server.server_id:
            raise LookupError(server_id)
        manifest = await self._server.discover(tool_name)
        self._assert_compatible(manifest.protocol_version)
        return manifest

    async def invoke(self, manifest: ToolManifest, arguments: dict[str, Any]) -> ToolResult:
        if manifest.required_scope not in self._allowed_scopes:
            raise ToolPermissionDenied(manifest.required_scope)
        self._assert_compatible(manifest.protocol_version)
        try:
            validate(arguments, manifest.input_schema)
        except ValidationError as error:
            raise ValueError(f"tool input schema validation failed: {error.message}") from error
        output = await asyncio.wait_for(
            self._server.invoke(manifest.tool_name, arguments),
            timeout=manifest.timeout_seconds,
        )
        provenance: dict[str, Any] = {
            "server_id": manifest.server_id,
            "tool_name": manifest.tool_name,
        }
        if manifest.sdk_version:
            provenance["sdk"] = manifest.sdk_version
        return ToolResult(
            output=output,
            provenance=provenance,
            protocol_version=manifest.protocol_version,
        )

    def _assert_compatible(self, version: str) -> None:
        if version in {
            self._supported_protocol_version,
            *self._legacy_protocol_versions,
        }:
            return
        raise ToolProtocolIncompatible(
            f"MCP {version} is not compatible with "
            f"{self._supported_protocol_version}; migrate the server or configure an "
            "explicit legacy compatibility path"
        )
