from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class MockEffectReceipt:
    external_id: str
    idempotency_key: str
    reconciled: bool


class MockEffectProvider:
    def __init__(self) -> None:
        self._receipts: dict[str, MockEffectReceipt] = {}
        self.call_count = 0

    async def execute_or_reconcile(self, idempotency_key: str) -> MockEffectReceipt:
        existing = self._receipts.get(idempotency_key)
        if existing is not None:
            return MockEffectReceipt(
                external_id=existing.external_id,
                idempotency_key=existing.idempotency_key,
                reconciled=True,
            )
        self.call_count += 1
        receipt = MockEffectReceipt(
            external_id=f"mock-{uuid4()}",
            idempotency_key=idempotency_key,
            reconciled=False,
        )
        self._receipts[idempotency_key] = receipt
        return receipt

