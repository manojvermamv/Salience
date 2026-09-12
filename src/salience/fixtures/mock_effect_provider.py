import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from salience.fixtures.mock_effect_memory import MockEffectProvider, MockEffectReceipt


class HttpMockEffectProvider:
    """HTTP adapter for the independently running no-op effect fixture."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def execute_or_reconcile(self, idempotency_key: str) -> MockEffectReceipt:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=5) as client:
            response = await client.post("/effects", json={"idempotency_key": idempotency_key})
        response.raise_for_status()
        payload = response.json()
        return MockEffectReceipt(
            external_id=payload["external_id"],
            idempotency_key=payload["idempotency_key"],
            reconciled=payload["reconciled"],
        )


class MockEffectRequest(BaseModel):
    idempotency_key: str


_provider = MockEffectProvider()
app = FastAPI(title="Salience mock external-effect provider")


@app.get("/health/live")
async def live() -> dict[str, str | int]:
    return {"status": "ok", "accepted_effect_count": _provider.call_count}


@app.post("/effects")
async def execute_effect(request: MockEffectRequest) -> MockEffectReceipt:
    return await _provider.execute_or_reconcile(request.idempotency_key)
