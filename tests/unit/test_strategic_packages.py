from salience.intelligence.contracts import OpportunityInput
from salience.intelligence.packages import PackageEvaluator, StrategicPackageGenerator


def _opportunity() -> OpportunityInput:
    return OpportunityInput(
        fingerprint="urban-garden-water",
        topic="Water-saving urban gardens",
        score=82,
        signal_ids=["signal-1"],
        explanation="Fresh source-linked audience interest",
    )


def test_package_generator_produces_materially_diverse_complete_candidates() -> None:
    candidates = StrategicPackageGenerator().generate(
        _opportunity(),
        {"audience": "apartment gardeners", "positioning": "practical guidance"},
        count=3,
    )

    assert len(candidates) == 3
    assert len({candidate.diversity_fingerprint for candidate in candidates}) == 3
    assert all(
        {
            "target_audience",
            "audience_problem_or_desire",
            "angle",
            "hook",
            "promise",
            "format",
            "expected_duration_or_size",
            "opening_visual_concept",
            "novelty",
            "evidence_requirements",
            "risk_notes",
        } <= candidate.content.keys()
        for candidate in candidates
    )


def test_evaluator_penalizes_misleading_hooks_and_keeps_explanation() -> None:
    candidate = StrategicPackageGenerator().generate(_opportunity(), {}, count=3)[0]
    misleading = candidate.__class__(
        diversity_fingerprint=candidate.diversity_fingerprint,
        content={**candidate.content, "hook": "Guaranteed secret results today"},
    )

    evaluation = PackageEvaluator().evaluate(
        misleading,
        evidence=[{"verification_status": "unverified"}],
        strategy={"positioning": "practical guidance"},
    )

    assert evaluation.score < 70
    assert "misleading" in evaluation.reason


def test_evaluator_selects_the_highest_scoring_package_with_a_reason() -> None:
    candidates = StrategicPackageGenerator().generate(_opportunity(), {}, count=3)
    evaluator = PackageEvaluator()
    selection = evaluator.select(
        [
            ("package-1", evaluator.evaluate(candidates[0], [{"verification_status": "verified"}], {})),
            ("package-2", evaluator.evaluate(candidates[1], [], {})),
        ]
    )

    assert selection.package_id == "package-1"
    assert "highest deterministic score" in selection.reason
