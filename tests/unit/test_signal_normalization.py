from datetime import UTC, datetime

from salience.intelligence.signals import SignalDeduplicator, normalize_fetched_source
from salience.research.contracts import FetchedSource


def source(source_id: str, raw_identity: str) -> FetchedSource:
    return FetchedSource(
        source_id=source_id,
        source_version="v1",
        source_type="fixture",
        resource_identity=raw_identity,
        canonical_url="HTTPS://Example.TEST/garden-trends#fragment",
        raw_identity=raw_identity,
        raw_hash="a" * 64,
        fetched_at=datetime(2026, 9, 12, tzinfo=UTC),
        published_at=datetime(2026, 9, 11, tzinfo=UTC),
        title="Garden Trends",
        content={"title": "Garden Trends"},
        raw_payload=b"fixture",
        features={"freshness": 0.8, "confidence": 0.7},
        rate_limit={},
        trust_level="untrusted_external",
        provenance={},
    )


def test_duplicate_resource_observations_merge_and_keep_all_supports() -> None:
    merged = SignalDeduplicator().merge(
        [normalize_fetched_source(source("rss", "guid-42")), normalize_fetched_source(source("hn", "123"))]
    )

    assert len(merged) == 1
    assert {support.source_id for support in merged[0].supports} == {"rss", "hn"}
    assert merged[0].canonical_url == "https://example.test/garden-trends"
    assert merged[0].feature_availability["missing"]
