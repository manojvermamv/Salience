"""Deterministic source normalization and support-preserving signal deduplication."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from salience.research.contracts import FetchedSource


SIGNAL_FEATURES = (
    "freshness",
    "velocity",
    "engagement",
    "source_diversity",
    "niche_relevance",
    "audience_relevance",
    "saturation",
    "novelty",
    "confidence",
    "risk",
)


@dataclass(frozen=True)
class SignalSupport:
    source_id: str
    resource_identity: str


@dataclass(frozen=True)
class CanonicalSignal:
    fingerprint: str
    topic: str
    canonical_url: str
    features: dict[str, float]
    feature_availability: dict[str, list[str]]
    supports: list[SignalSupport]


def normalize_fetched_source(source: FetchedSource) -> CanonicalSignal:
    canonical_url = _canonical_url(source.canonical_url)
    topic = (source.title or source.content.get("title") or source.resource_identity).strip()
    features = {
        name: _clamp(value)
        for name, value in source.features.items()
        if name in SIGNAL_FEATURES and isinstance(value, int | float)
    }
    return CanonicalSignal(
        fingerprint=_fingerprint(canonical_url or topic),
        topic=topic,
        canonical_url=canonical_url,
        features=features,
        feature_availability={
            "present": sorted(features),
            "missing": sorted(set(SIGNAL_FEATURES) - set(features)),
        },
        supports=[
            SignalSupport(
                source_id=source.source_id, resource_identity=source.resource_identity
            )
        ],
    )


class SignalDeduplicator:
    def merge(self, signals: list[CanonicalSignal]) -> list[CanonicalSignal]:
        groups: dict[str, list[CanonicalSignal]] = {}
        for signal in signals:
            key = signal.canonical_url or signal.fingerprint
            groups.setdefault(key, []).append(signal)
        return [self._merge_group(group) for group in groups.values()]

    @staticmethod
    def _merge_group(group: list[CanonicalSignal]) -> CanonicalSignal:
        first = group[0]
        features: dict[str, float] = {}
        supports: list[SignalSupport] = []
        for signal in group:
            for name, value in signal.features.items():
                features[name] = max(features.get(name, 0), value)
            supports.extend(signal.supports)
        unique_supports = list(
            {
                (support.source_id, support.resource_identity): support
                for support in supports
            }.values()
        )
        features["source_diversity"] = _clamp(len({support.source_id for support in unique_supports}) / 3)
        return CanonicalSignal(
            fingerprint=_fingerprint(first.canonical_url or first.topic),
            topic=first.topic,
            canonical_url=first.canonical_url,
            features=features,
            feature_availability={
                "present": sorted(features),
                "missing": sorted(set(SIGNAL_FEATURES) - set(features)),
            },
            supports=unique_supports,
        )


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    netloc = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _fingerprint(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def _clamp(value: int | float) -> float:
    return max(0.0, min(1.0, float(value)))
