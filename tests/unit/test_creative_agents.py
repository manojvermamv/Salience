import pytest

from salience.agents.execution import AgentInvocation
from salience.agents.fixtures import fixture_agent_service


@pytest.mark.asyncio
async def test_writer_direct_and_delegated_outputs_use_the_same_script_contract() -> None:
    service = fixture_agent_service()
    brief_input = {
        "brief_id": "brief-1",
        "content_program_id": "program-1",
        "claim_ids": ["claim-1"],
        "evidence_ids": ["evidence-1"],
        "target_format": "short_video",
        "target_duration_seconds": 30,
    }

    direct = await service.invoke_by_id("writer_agent", brief_input)
    delegated = await service.invoke_from_parent(
        "lead_content_agent", AgentInvocation("writer_agent", brief_input)
    )

    assert direct.output["contract_version"] == "ScriptVersion@v1"
    assert delegated.output == direct.output
    assert delegated.execution_context is not None
    assert delegated.execution_context.tool_scopes == frozenset()


@pytest.mark.asyncio
async def test_production_agent_caps_requested_variants_and_has_no_publish_scope() -> None:
    service = fixture_agent_service()

    run = await service.invoke_by_id(
        "production_agent",
        {
            "brief_id": "brief-1",
            "script_id": "script-1",
            "capability": "text_to_video",
            "max_variants": 4,
        },
    )

    assert run.output["contract_version"] == "ProductionPlan@v1"
    assert run.output["requested_variants"] == 3
    assert run.execution_context is not None
    assert "publish" not in run.execution_context.tool_scopes


@pytest.mark.asyncio
async def test_creative_director_and_verifier_remain_provider_neutral() -> None:
    service = fixture_agent_service()

    direction = await service.invoke_by_id(
        "creative_director_agent", {"brief_id": "brief-1", "script_id": "script-1"}
    )
    advisory = await service.invoke_by_id(
        "verifier_agent", {"subject_id": "script-1", "subject_type": "script"}
    )

    assert direction.output["provider_id"] is None
    assert advisory.output["advisory_only"] is True
