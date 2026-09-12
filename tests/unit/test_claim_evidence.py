from salience.intelligence.claims import ClaimEvidence, ClaimVerifier
from salience.intelligence.contracts import ClaimInput


def _claim() -> ClaimInput:
    return ClaimInput(
        fingerprint="rain-capture",
        text="Rain capture reduces municipal water use for container gardens.",
    )


def test_claim_with_only_contradictory_evidence_is_not_verified() -> None:
    result = ClaimVerifier().link(
        _claim(),
        [
            ClaimEvidence(
                evidence_id="evidence-1",
                relation="contradicting",
                verification_status="verified",
                source_trust="untrusted_external",
            )
        ],
    )

    assert result.claim.verification_status == "contradicted"
    assert result.links == (("evidence-1", "contradicting"),)


def test_claim_needs_verified_support_before_it_can_be_used_as_verified() -> None:
    result = ClaimVerifier().link(
        _claim(),
        [
            ClaimEvidence(
                evidence_id="evidence-1",
                relation="supporting",
                verification_status="verified",
                source_trust="untrusted_external",
            )
        ],
    )

    assert result.claim.verification_status == "verified"
