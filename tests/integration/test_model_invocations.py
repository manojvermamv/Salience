from uuid import uuid4

import httpx
import pytest

from salience.models.contracts import ModelRequest
from salience.models.openai_compatible import OpenAICompatibleAdapter
from salience.models.recording import InMemoryModelInvocationRepository, RecordedModelGateway
from salience.observability.tracing import TraceContext


@pytest.mark.asyncio
async def test_openai_compatible_usage_and_lineage_are_recorded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fixture-secret"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"topic":"gardening"}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    repository = InMemoryModelInvocationRepository()
    gateway = RecordedModelGateway(
        gateway=OpenAICompatibleAdapter(
            runtime_id="fixture-runtime",
            base_url="https://models.fixture/v1",
            model="fixture-model",
            api_key="fixture-secret",
            http_client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ),
        ),
        repository=repository,
    )
    parent_agent_run_id = uuid4()

    result = await gateway.complete(
        ModelRequest(
            prompt="extract a topic",
            capability="research.extract",
            parent_agent_run_id=parent_agent_run_id,
            trace_context=TraceContext.new_root(),
            input_artifact_reference="object://research/request.json",
            output_schema={"type": "object", "required": ["topic"]},
        )
    )

    invocation = await repository.latest()
    assert result.provider_metadata["model"] == "fixture-model"
    assert result.actual_cost_micros == 0
    assert result.output_hash == invocation.output_hash
    assert invocation.parent_agent_run_id == parent_agent_run_id
    assert invocation.input_artifact_reference == "object://research/request.json"
    assert invocation.status == "succeeded"
