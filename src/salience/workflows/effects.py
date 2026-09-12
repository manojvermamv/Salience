from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EffectRequest:
    idempotency_key: str
    effect_type: str
    dry_run: bool


@dataclass(frozen=True)
class EffectReceipt:
    external_id: str
    idempotency_key: str
    reconciled: bool


class EffectProvider(Protocol):
    async def execute_or_reconcile(self, idempotency_key: str) -> EffectReceipt: ...


class ExternalEffectService:
    def __init__(self, provider: EffectProvider) -> None:
        self._provider = provider

    async def execute_or_reconcile(self, request: EffectRequest) -> EffectReceipt:
        if request.dry_run:
            return EffectReceipt(
                external_id=f"dry-run:{request.idempotency_key}",
                idempotency_key=request.idempotency_key,
                reconciled=False,
            )
        return await self._provider.execute_or_reconcile(request.idempotency_key)

