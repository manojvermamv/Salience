from uuid import uuid4

import pytest

from salience.a2a.fixture_agent import FixtureA2AAgent
from salience.a2a.gateway import RemoteAgentGateway, RemoteProtocolIncompatible


@pytest.mark.asyncio
async def test_a2a_fixture_maps_result_and_parent_lineage() -> None:
    gateway = RemoteAgentGateway(
        agent=FixtureA2AAgent(), supported_protocol_version="0.3.0"
    )
    parent_run_id = uuid4()

    agent = await gateway.discover("fixture-a2a")
    result = await gateway.invoke(agent, {"niche": "finance"}, parent_run_id)

    assert result.parent_run_id == parent_run_id
    assert result.output["source"] == "fixture"
    assert result.artifacts[0]["kind"] == "structured-data"


@pytest.mark.asyncio
async def test_a2a_gateway_rejects_incompatible_agent_card_before_invocation() -> None:
    gateway = RemoteAgentGateway(
        agent=FixtureA2AAgent(), supported_protocol_version="0.2.0"
    )

    with pytest.raises(RemoteProtocolIncompatible):
        await gateway.discover("fixture-a2a")
