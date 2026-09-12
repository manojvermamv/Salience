import pytest

from mcp.server.mcpserver import MCPServer

from salience.mcp.gateway import ToolGateway
from salience.mcp.sdk_adapter import McpSdkAdapter


@pytest.mark.asyncio
async def test_mcp_sdk_adapter_discovers_and_calls_2026_tool() -> None:
    server = MCPServer("phase-five-fixture")

    @server.tool(name="research.fetch")
    def research_fetch(query: str) -> dict[str, str]:
        return {"query": query, "source": "mcp-sdk-fixture"}

    adapter = McpSdkAdapter(
        server_id="phase-five-fixture",
        endpoint=server,
        tool_scopes={"research.fetch": "research.fetch"},
        timeout_seconds=2,
    )
    gateway = ToolGateway(
        server=adapter,
        allowed_scopes=frozenset({"research.fetch"}),
        supported_protocol_version="2026-07-28",
    )

    manifest = await gateway.discover("phase-five-fixture", "research.fetch")
    result = await gateway.invoke(manifest, {"query": "climate"})

    assert manifest.protocol_version == "2026-07-28"
    assert manifest.protocol_features == ("server/discover", "tools/call")
    assert manifest.authentication_mode == "adapter-edge"
    assert result.output == {"query": "climate", "source": "mcp-sdk-fixture"}
    assert result.provenance["sdk"] == "mcp==2.2.0"


@pytest.mark.asyncio
async def test_mcp_2025_fixture_is_explicit_legacy_compatibility() -> None:
    from salience.mcp.fixture_server import FixtureMcpServer

    gateway = ToolGateway(
        server=FixtureMcpServer(),
        allowed_scopes=frozenset({"tool:niche.lookup"}),
        supported_protocol_version="2026-07-28",
        legacy_protocol_versions=frozenset({"2025-11-25"}),
    )

    manifest = await gateway.discover("fixture-mcp", "niche.lookup")
    result = await gateway.invoke(manifest, {"niche": "climate"})

    assert result.protocol_version == "2025-11-25"
