import pytest

from salience.agents.fixtures import fixture_agent_service
from salience.models.static import StaticModelAdapter


@pytest.mark.asyncio
async def test_same_agent_runs_through_two_runtime_configurations() -> None:
    service = fixture_agent_service(
        model_gateways={
            "static-v1": StaticModelAdapter(runtime_id="static-v1"),
            "static-v2": StaticModelAdapter(runtime_id="static-v2"),
        }
    )

    first = await service.invoke_with_runtime(
        "research_agent", "static-v1", {"niche": "finance"}
    )
    second = await service.invoke_with_runtime(
        "research_agent", "static-v2", {"niche": "finance"}
    )

    assert first.agent_id == second.agent_id == "research_agent"
    assert first.runtime_id == "static-v1"
    assert second.runtime_id == "static-v2"
