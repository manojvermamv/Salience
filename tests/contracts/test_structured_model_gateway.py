import pytest

from salience.models.contracts import ModelRequest
from salience.models.gateway import ModelOutputInvalidError
from salience.models.recording import InMemoryModelInvocationRepository, RecordedModelGateway
from salience.models.static import StaticModelAdapter


@pytest.mark.asyncio
async def test_invalid_model_json_is_recorded_and_cannot_become_agent_output() -> None:
    repository = InMemoryModelInvocationRepository()
    gateway = RecordedModelGateway(
        gateway=StaticModelAdapter(runtime_id="invalid-fixture", valid_output=False),
        repository=repository,
    )

    with pytest.raises(ModelOutputInvalidError):
        await gateway.complete(
            ModelRequest(
                prompt="never-persist-this-model-prompt",
                capability="research.extract",
                output_schema={"type": "object", "required": ["topic"]},
            )
        )

    invocation = await repository.latest()
    assert invocation.status == "invalid_output"
    assert invocation.input_hash
    assert invocation.output_hash is None
    assert "never-persist-this-model-prompt" not in str(invocation)
