from salience.intelligence.scoring import OpportunityRanker
from salience.intelligence.signals import CanonicalSignal, SignalSupport


def test_signal_eval_preserves_source_support_and_missing_fact_boundaries() -> None:
    signal = CanonicalSignal(
        fingerprint="fixture",
        topic="Fixture topic",
        canonical_url="https://example.test/fixture",
        features={"niche_relevance": 0.9},
        feature_availability={"present": ["niche_relevance"], "missing": []},
        supports=[
            SignalSupport(source_id="rss", resource_identity="one"),
            SignalSupport(source_id="hn", resource_identity="two"),
        ],
    )

    opportunity = OpportunityRanker().rank([signal], [])[0]

    assert len(signal.supports) == 2
    assert "velocity" in opportunity.feature_availability["missing"]
    assert opportunity.semantic_adjustment == 0
