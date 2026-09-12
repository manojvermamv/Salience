import pytest

from salience.mcp.fixture_server import FixtureMcpServer
from salience.mcp.gateway import ToolGateway, ToolPermissionDenied


@pytest.mark.asyncio
async def test_mcp_gateway_records_provenanced_tool_call() -> None:
    gateway = ToolGateway(
        server=FixtureMcpServer(),
        allowed_scopes=frozenset({"tool:niche.lookup"}),
        supported_protocol_version="2025-11-25",
    )

    tool = await gateway.discover("fixture-mcp", "niche.lookup")
    result = await gateway.invoke(tool, {"niche": "personal finance"})

    assert result.output["source"] == "fixture"
    assert result.provenance["server_id"] == "fixture-mcp"
    assert result.protocol_version == "2025-11-25"


@pytest.mark.asyncio
async def test_mcp_gateway_denies_missing_scope() -> None:
    gateway = ToolGateway(
        server=FixtureMcpServer(),
        allowed_scopes=frozenset(),
        supported_protocol_version="2025-11-25",
    )
    tool = await gateway.discover("fixture-mcp", "niche.lookup")

    with pytest.raises(ToolPermissionDenied):
        await gateway.invoke(tool, {"niche": "personal finance"})
