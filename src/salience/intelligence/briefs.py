"""Immutable Phase 6 content-brief assembly; no script text is generated here."""

from __future__ import annotations

import hashlib
from typing import Iterable

from salience.intelligence.contracts import ClaimInput, ContentBriefInput, OpportunityInput, PackageInput


class ContentBriefAssembler:
    def create(
        self,
        *,
        program_id: str,
        opportunity_id: str,
        opportunity: OpportunityInput,
        package_id: str,
        package: PackageInput,
        strategy_version_id: str | None,
        claims: Iterable[ClaimInput | tuple[str, ClaimInput]],
    ) -> ContentBriefInput:
        verified_claim_ids: list[str] = []
        required_claims: list[str] = []
        prohibited_claims: list[str] = []
        for item in claims:
            claim_id, claim = item if isinstance(item, tuple) else (item.fingerprint, item)
            if claim.verification_status == "verified":
                verified_claim_ids.append(claim_id)
                required_claims.append(claim.text)
            else:
                prohibited_claims.append(claim.text)
        brief_key = hashlib.sha256(
            f"{program_id}:{opportunity_id}:{package_id}".encode()
        ).hexdigest()[:32]
        return ContentBriefInput(
            brief_key=brief_key,
            version=1,
            opportunity_id=opportunity_id,
            package_id=package_id,
            strategy_version_id=strategy_version_id,
            claim_ids=verified_claim_ids,
            content={
                "contract_version": "ContentBrief@v1",
                "topic": opportunity.topic,
                "opportunity_explanation": opportunity.explanation,
                "audience": package.content["target_audience"],
                "intent": package.content["audience_problem_or_desire"],
                "promise": package.content["promise"],
                "format": package.content["format"],
                "creative_direction": package.content["opening_visual_concept"],
                "strategic_package": package.content,
                "required_claims": required_claims,
                "prohibited_claims": prohibited_claims,
                "source_references": opportunity.signal_ids,
                "known_risks": opportunity.risks + list(package.content["risk_notes"]),
                "open_research_questions": [
                    "Confirm any external factual claim before scripting.",
                ],
                "recommended_next_action": "phase_7_review",
            },
            provenance={
                "assembler": "content-brief-v1",
                "program_id": program_id,
                "package_fingerprint": package.diversity_fingerprint,
            },
        )
