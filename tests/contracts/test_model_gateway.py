import pytest

from salience.models.contracts import ModelRequest
from salience.models.gateway import ModelDisabledError
from salience.models.static import StaticModelAdapter


@pytest.mark.asyncio
async def test_static_model_gateway_returns_provider_neutral_usage_metadata() -> None:
    gateway = StaticModelAdapter(runtime_id="static-v1")

    result = await gateway.complete(
        ModelRequest(prompt="Research personal finance", output_schema={"type": "object"})
    )

    assert result.runtime_id == "static-v1"
    assert result.output["source"] == "static-fixture"
    assert result.usage["input_tokens"] > 0


@pytest.mark.asyncio
async def test_disabled_model_gateway_fails_without_network_access() -> None:
    gateway = StaticModelAdapter(runtime_id="disabled", enabled=False)

    with pytest.raises(ModelDisabledError):
        await gateway.complete(ModelRequest(prompt="nope", output_schema={"type": "object"}))
