from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True)
class ScopeGrant:
    subject_id: UUID
    scopes: frozenset[str]
    expires_at: datetime | None = None
    enabled: bool = True

    def permits(self, scope: str, *, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(UTC)
        return (
            self.enabled
            and (self.expires_at is None or self.expires_at > current_time)
            and scope in self.scopes
        )

