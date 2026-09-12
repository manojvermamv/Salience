from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class NetworkScopeDenied(PermissionError):
    """Raised before a connector leaves its configured network allowlist."""


class SourceFetchError(RuntimeError):
    """A bounded source failure that cannot be converted into a signal."""


@dataclass(frozen=True)
class SourceFetchRequest:
    source_id: str
    source_version: str
    url: str
    allowed_domains: frozenset[str]
    timeout_seconds: float
    max_response_bytes: int
    cursor: str | None = None
    window_key: str | None = None


@dataclass(frozen=True)
class FetchedSource:
    source_id: str
    source_version: str
    source_type: str
    resource_identity: str
    canonical_url: str
    raw_identity: str
    raw_hash: str
    fetched_at: datetime
    published_at: datetime | None
    title: str | None
    content: dict[str, Any]
    raw_payload: bytes
    features: dict[str, int | float]
    rate_limit: dict[str, str]
    trust_level: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ResearchFinding:
    source_uri: str
    fetched_at: datetime
    content: dict[str, object]
    trust_level: str = "fixture"
    verification_status: str = "verified"
    source_identity: str | None = None
    provenance: dict[str, object] = field(default_factory=dict)
    raw_content_classification: str = "internal"


class ResearchConnector(Protocol):
    async def research(self, niche: str) -> list[ResearchFinding]: ...


class ResearchSourceConnector(Protocol):
    async def fetch(self, request: SourceFetchRequest) -> list[FetchedSource]: ...
