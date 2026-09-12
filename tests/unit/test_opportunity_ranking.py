from salience.intelligence.scoring import OpportunityRanker
from salience.intelligence.signals import CanonicalSignal, SignalSupport


def test_ranker_never_invents_missing_metrics_and_explains_score() -> None:
    signal = CanonicalSignal(
        fingerprint="garden-trends",
        topic="Garden trends",
        canonical_url="https://example.test/garden-trends",
        features={"freshness": 0.8, "confidence": 0.6},
        feature_availability={"present": ["freshness", "confidence"], "missing": []},
        supports=[SignalSupport(source_id="rss", resource_identity="guid-42")],
    )

    opportunity = OpportunityRanker().rank([signal], [])[0]

    assert "engagement" not in opportunity.features
    assert "engagement" in opportunity.feature_availability["missing"]
    assert 0 <= opportunity.score <= 100
    assert "deterministic" in opportunity.explanation


def test_semantic_adjustment_is_bounded_and_cannot_replace_base_score() -> None:
    signal = CanonicalSignal(
        fingerprint="garden-trends",
        topic="Garden trends",
        canonical_url="https://example.test/garden-trends",
        features={"freshness": 1, "velocity": 1, "source_diversity": 1, "niche_relevance": 1,
                  "audience_relevance": 1, "novelty": 1, "confidence": 1, "saturation": 0, "risk": 0},
        feature_availability={"present": [], "missing": []},
        supports=[SignalSupport(source_id="rss", resource_identity="guid-42")],
    )

    opportunity = OpportunityRanker().rank(
        [signal], [{"fingerprint": "garden-trends", "adjustment": 500, "explanation": "invalid"}]
    )[0]

    assert opportunity.semantic_adjustment == 10
    assert opportunity.score == 100
