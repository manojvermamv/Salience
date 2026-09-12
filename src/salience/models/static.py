from jsonschema import ValidationError, validate

from salience.models.contracts import ModelRequest, ModelResult
from salience.models.gateway import ModelDisabledError, ModelOutputInvalidError


class StaticModelAdapter:
    def __init__(
        self, *, runtime_id: str, enabled: bool = True, valid_output: bool = True
    ) -> None:
        self._runtime_id = runtime_id
        self._enabled = enabled
        self._valid_output = valid_output

    async def complete(self, request: ModelRequest) -> ModelResult:
        if not self._enabled:
            raise ModelDisabledError(f"model runtime {self._runtime_id} is disabled")
        output = (
            {"source": "static-fixture", "prompt": request.prompt}
            if self._valid_output
            else {"invalid": True}
        )
        try:
            validate(output, request.output_schema)
        except ValidationError as error:
            raise ModelOutputInvalidError(str(error)) from error
        return ModelResult(
            runtime_id=self._runtime_id,
            output=output,
            usage={"input_tokens": max(1, len(request.prompt.split())), "output_tokens": 2},
            latency_ms=0,
            provider_metadata={"adapter": "static"},
            provider="static",
            model="fixture",
        )
