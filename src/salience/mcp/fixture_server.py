from typing import Any

from salience.mcp.contracts import ToolManifest


class FixtureMcpServer:
    server_id = "fixture-mcp"
    protocol_version = "2025-11-25"

    async def discover(self, tool_name: str) -> ToolManifest:
        if tool_name != "niche.lookup":
            raise LookupError(tool_name)
        return ToolManifest(
            server_id=self.server_id,
            tool_name=tool_name,
            input_schema={"type": "object", "required": ["niche"]},
            required_scope="tool:niche.lookup",
            protocol_version=self.protocol_version,
            timeout_seconds=2,
        )

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "niche.lookup":
            raise LookupError(tool_name)
        return {"niche": arguments["niche"], "source": "fixture"}
