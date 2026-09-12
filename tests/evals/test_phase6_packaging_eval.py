from salience.intelligence.briefs import ContentBriefAssembler
from salience.intelligence.claims import ClaimEvidence, ClaimVerifier
from salience.intelligence.contracts import ClaimInput, OpportunityInput
from salience.intelligence.packages import StrategicPackageGenerator


def test_phase_six_brief_exposes_unsupported_claims_as_prohibited_not_facts() -> None:
    opportunity = OpportunityInput(
        fingerprint="garden",
        topic="Urban gardens",
        score=80,
        signal_ids=["signal-1"],
        explanation="Evidence exists",
    )
    package = StrategicPackageGenerator().generate(opportunity, {}, count=3)[0]
    claim = ClaimVerifier().link(
        ClaimInput(fingerprint="garden-claim", text="External text says something."),
        [
            ClaimEvidence(
                evidence_id="evidence-1",
                relation="supporting",
                verification_status="unverified",
                source_trust="untrusted_external",
            )
        ],
    ).claim

    brief = ContentBriefAssembler().create(
        program_id="program-1",
        opportunity_id="opportunity-1",
        opportunity=opportunity,
        package_id="package-1",
        package=package,
        strategy_version_id="strategy-1",
        claims=[claim],
    )

    assert brief.claim_ids == []
    assert brief.content["prohibited_claims"] == [claim.text]
    assert brief.content["recommended_next_action"] == "phase_7_review"
