"""Provider-neutral inputs for canonical intelligence-loop records."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceInput:
    source_key: str
    version: str
    connector: str
    trust_level: str
    configuration: dict[str, Any]
    network_scope: list[str] = field(default_factory=list)
    rate_limit: dict[str, Any] = field(default_factory=dict)
    protocol_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchInput:
    source_id: str
    resource_identity: str
    window_key: str
    request_fingerprint: str
    canonical_url: str
    raw_hash: str | None
    cursor: str | None = None
    status: str = "succeeded"
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalInput:
    fingerprint: str
    topic: str
    features: dict[str, Any]
    availability: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpportunityInput:
    fingerprint: str
    topic: str
    score: float
    signal_ids: list[str]
    explanation: str
    status: str = "candidate"
    base_score: float | None = None
    semantic_adjustment: float | None = None
    risks: list[str] = field(default_factory=list)
    availability: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PackageInput:
    diversity_fingerprint: str
    content: dict[str, Any]
    status: str = "candidate"
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PackageEvaluationInput:
    evaluation_key: str
    score: float
    reason: str
    semantic_score: float | None = None
    status: str = "evaluated"
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimInput:
    fingerprint: str
    text: str
    verification_status: str = "unverified"
    confidence: float = 0.5
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContentBriefInput:
    brief_key: str
    version: int
    opportunity_id: str
    package_id: str
    claim_ids: list[str]
    content: dict[str, Any]
    strategy_version_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
