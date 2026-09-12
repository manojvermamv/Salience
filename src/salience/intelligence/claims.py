"""Claim/evidence linkage that never promotes unverified external text to fact."""

from __future__ import annotations

from dataclasses import dataclass

from salience.intelligence.contracts import ClaimInput


@dataclass(frozen=True)
class ClaimEvidence:
    evidence_id: str
    relation: str
    verification_status: str
    source_trust: str

    def __post_init__(self) -> None:
        if self.relation not in {"supporting", "contradicting"}:
            raise ValueError("claim evidence relation must be supporting or contradicting")


@dataclass(frozen=True)
class ClaimVerification:
    claim: ClaimInput
    links: tuple[tuple[str, str], ...]


class ClaimVerifier:
    def link(self, claim: ClaimInput, evidence: list[ClaimEvidence]) -> ClaimVerification:
        has_contradiction = any(item.relation == "contradicting" for item in evidence)
        has_verified_support = any(
            item.relation == "supporting" and item.verification_status == "verified"
            for item in evidence
        )
        if has_contradiction:
            status = "contradicted"
            confidence = 0.0
        elif has_verified_support:
            status = "verified"
            confidence = 0.8
        else:
            status = "unverified"
            confidence = 0.5
        return ClaimVerification(
            claim=ClaimInput(
                fingerprint=claim.fingerprint,
                text=claim.text,
                verification_status=status,
                confidence=confidence,
                provenance={
                    **claim.provenance,
                    "evidence_relations": [
                        {"evidence_id": item.evidence_id, "relation": item.relation}
                        for item in evidence
                    ],
                },
            ),
            links=tuple((item.evidence_id, item.relation) for item in evidence),
        )
