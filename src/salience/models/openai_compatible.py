import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from time import perf_counter

import httpx
from jsonschema import ValidationError, validate

from salience.models.contracts import ModelRequest, ModelResult
from salience.models.gateway import ModelDisabledError, ModelGatewayError, ModelOutputInvalidError


class OpenAICompatibleAdapter:
    """Minimal OpenAI-compatible JSON transport; credentials stay at this edge."""

    def __init__(
        self,
        *,
        runtime_id: str,
        base_url: str,
        model: str,
        api_key: str | None,
        enabled: bool = True,
        http_client_factory: Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]
        | None = None,
    ) -> None:
        self._runtime_id = runtime_id
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._enabled = enabled
        self._http_client_factory = http_client_factory or self._new_http_client

    def _new_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15)

    async def complete(self, request: ModelRequest) -> ModelResult:
        if not self._enabled or not self._api_key:
            raise ModelDisabledError(f"model runtime {self._runtime_id} is disabled")
        started = perf_counter()
        async with self._http_client_factory() as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": request.prompt}],
                    "response_format": {"type": "json_object"},
                },
            )
        if response.is_error:
            raise ModelGatewayError(f"model request failed with {response.status_code}")
        payload = response.json()
        try:
            output = json.loads(payload["choices"][0]["message"]["content"])
            validate(output, request.output_schema)
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ModelOutputInvalidError("invalid OpenAI-compatible response") from error
        except ValidationError as error:
            raise ModelOutputInvalidError(str(error)) from error
        usage = payload.get("usage", {})
        return ModelResult(
            runtime_id=self._runtime_id,
            output=output,
            usage={
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
            },
            latency_ms=round((perf_counter() - started) * 1000),
            provider_metadata={"adapter": "openai-compatible", "model": self._model},
            provider="openai-compatible",
            model=self._model,
        )
