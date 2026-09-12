"""Official MCP SDK adapter projected into Salience-owned tool contracts."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from mcp import Client

from salience.mcp.contracts import ToolManifest


class McpSdkAdapterError(ValueError):
    """Raised when an MCP SDK response cannot safely enter owned contracts."""


class McpSdkAdapter:
    """Use MCP v2 discovery and read-only calls without leaking SDK values."""

    def __init__(
        self,
        *,
        server_id: str,
        endpoint: Any,
        tool_scopes: Mapping[str, str],
        timeout_seconds: float,
        client_factory: Callable[[], AbstractAsyncContextManager[Client]] | None = None,
    ) -> None:
        self.server_id = server_id
        self.protocol_version = "unresolved"
        self._endpoint = endpoint
        self._tool_scopes = dict(tool_scopes)
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or self._new_client
        self._discovered_protocol_version: str | None = None

    def _new_client(self) -> Client:
        return Client(
            self._endpoint,
            mode="auto",
            read_timeout_seconds=self._timeout_seconds,
        )

    async def discover(self, tool_name: str) -> ToolManifest:
        required_scope = self._tool_scopes.get(tool_name)
        if required_scope is None:
            raise PermissionError(f"tool {tool_name} is not configured for an owned scope")

        async with self._client_factory() as client:
            tools = await client.list_tools()
            tool = next((candidate for candidate in tools.tools if candidate.name == tool_name), None)
            if tool is None:
                raise LookupError(tool_name)
            protocol_version = _protocol_version(client)

        self.protocol_version = protocol_version
        self._discovered_protocol_version = protocol_version
        return ToolManifest(
            server_id=self.server_id,
            tool_name=tool.name,
            input_schema=_json_object(tool.input_schema, "tool input schema"),
            required_scope=required_scope,
            protocol_version=protocol_version,
            timeout_seconds=self._timeout_seconds,
            protocol_features=("server/discover", "tools/call"),
            authentication_mode="adapter-edge",
            sdk_version="mcp==2.2.0",
        )

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in self._tool_scopes:
            raise PermissionError(f"tool {tool_name} is not configured for an owned scope")
        async with self._client_factory() as client:
            protocol_version = _protocol_version(client)
            if (
                self._discovered_protocol_version is not None
                and protocol_version != self._discovered_protocol_version
            ):
                raise McpSdkAdapterError(
                    "MCP protocol revision changed after discovery; rediscover before calling"
                )
            result = await client.call_tool(
                tool_name,
                _json_object(arguments, "tool arguments"),
                read_timeout_seconds=self._timeout_seconds,
            )

        if result.is_error:
            raise McpSdkAdapterError(f"MCP tool {tool_name} returned an error")
        if isinstance(result.structured_content, dict):
            return _json_object(result.structured_content, "structured tool result")
        return {
            "content": _json_value(
                [
                    content.model_dump(mode="json", by_alias=True)
                    for content in result.content
                ],
                "tool result content",
            )
        }


def _protocol_version(client: Client) -> str:
    session = client.session
    version = session.protocol_version if session is not None else None
    if not version:
        raise McpSdkAdapterError("MCP client did not negotiate a protocol revision")
    return version


def _json_object(value: Any, label: str) -> dict[str, Any]:
    compatible_value = _json_value(value, label)
    if not isinstance(compatible_value, dict):
        raise McpSdkAdapterError(f"{label} must be a JSON object")
    return compatible_value


def _json_value(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError) as error:
        raise McpSdkAdapterError(f"{label} must be JSON-compatible") from error
