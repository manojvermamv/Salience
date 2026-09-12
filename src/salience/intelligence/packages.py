"""Deterministic strategic-package generation and transparent evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from salience.intelligence.contracts import OpportunityInput, PackageEvaluationInput, PackageInput


@dataclass(frozen=True)
class PackageSelection:
    package_id: str
    reason: str


class StrategicPackageGenerator:
    """Generate materially different packaging directions before any scripting."""

    def generate(
        self,
        opportunity: OpportunityInput,
        strategy: dict[str, Any],
        count: int,
    ) -> list[PackageInput]:
        if count < 3 or count > 5:
            raise ValueError("strategic package count must be between 3 and 5")
        audience = str(strategy.get("audience") or "people seeking practical guidance")
        topic = opportunity.topic
        directions = (
            {
                "angle": "practical checklist",
                "hook": f"The practical {topic} checklist to use before your next decision",
                "promise": "Leave with a bounded, evidence-aware next step.",
                "format": "checklist article",
                "expected_duration_or_size": "800-1,000 words",
                "opening_visual_concept": "A labeled before-and-after decision board.",
                "novelty": "Turns a broad topic into an immediately usable checklist.",
            },
            {
                "angle": "trade-off explainer",
                "hook": f"Which {topic} choice fits your actual constraints?",
                "promise": "Compare options without pretending one fits everyone.",
                "format": "decision carousel",
                "expected_duration_or_size": "7 panels",
                "opening_visual_concept": "A split-path diagram with visible trade-offs.",
                "novelty": "Frames the topic as a transparent decision rather than a trend.",
            },
            {
                "angle": "common misconception audit",
                "hook": f"Three assumptions about {topic} worth checking first",
                "promise": "Separate useful evidence from tempting but unsupported claims.",
                "format": "short explainer video",
                "expected_duration_or_size": "90 seconds",
                "opening_visual_concept": "Three claim cards marked verify, uncertain, or reject.",
                "novelty": "Makes uncertainty visible instead of manufacturing certainty.",
            },
            {
                "angle": "small experiment guide",
                "hook": f"Test one {topic} change safely this week",
                "promise": "Run a small reversible experiment and document what happened.",
                "format": "field-note newsletter",
                "expected_duration_or_size": "600 words",
                "opening_visual_concept": "A one-week experiment calendar with observation slots.",
                "novelty": "Centers original observation rather than copied competitor structure.",
            },
            {
                "angle": "audience question answer",
                "hook": f"What should a beginner ask before acting on {topic}?",
                "promise": "Identify the questions that prevent a costly wrong turn.",
                "format": "Q&A guide",
                "expected_duration_or_size": "5 questions",
                "opening_visual_concept": "A conversation map moving from question to evidence.",
                "novelty": "Uses audience uncertainty as a guide for clear research questions.",
            },
        )
        candidates = [
            self._candidate(direction, topic=topic, audience=audience)
            for direction in directions[:count]
        ]
        fingerprints = [candidate.diversity_fingerprint for candidate in candidates]
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError("materially duplicate strategic package candidates")
        return candidates

    @staticmethod
    def _candidate(
        direction: dict[str, str], *, topic: str, audience: str
    ) -> PackageInput:
        content = {
            "topic": topic,
            "target_audience": audience,
            "audience_problem_or_desire": "Make a useful, low-risk decision with incomplete information.",
            **direction,
            "evidence_requirements": [
                "Link each factual claim to a source-linked evidence record.",
                "Mark unsupported or contradictory claims as prohibited.",
            ],
            "risk_notes": [
                "Do not convert external source text into a verified fact without evidence review.",
                "Avoid misleading certainty or clickbait promises.",
            ],
        }
        fingerprint = _fingerprint(
            content,
            keys=("target_audience", "angle", "hook", "promise", "format"),
        )
        return PackageInput(
            diversity_fingerprint=fingerprint,
            content=content,
            provenance={"generator": "deterministic-packages-v1"},
        )


class PackageEvaluator:
    """Apply deterministic quality/risk constraints before optional semantic scores."""

    def evaluate(
        self,
        package: PackageInput,
        evidence: list[dict[str, Any]],
        strategy: dict[str, Any],
        semantic_score: float | None = None,
    ) -> PackageEvaluationInput:
        required = {
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
        }
        missing = sorted(required - package.content.keys())
        score = 100.0 - 10 * len(missing)
        reasons: list[str] = []
        if missing:
            reasons.append(f"missing required fields: {', '.join(missing)}")
        if not any(item.get("verification_status") == "verified" for item in evidence):
            score -= 18
            reasons.append("evidence remains unverified")
        hook = str(package.content.get("hook", "")).lower()
        if any(marker in hook for marker in ("guaranteed", "secret", "shocking", "must-see")):
            score -= 30
            reasons.append("misleading hook terms")
        positioning = str(strategy.get("positioning", "")).lower()
        if positioning and "evidence" in positioning and not package.content.get("evidence_requirements"):
            score -= 15
            reasons.append("does not meet evidence-grounded positioning")
        bounded_semantic = _bounded_semantic_score(semantic_score)
        if bounded_semantic is not None:
            score = (score * 0.8) + (bounded_semantic * 0.2)
            reasons.append("bounded semantic judgment included")
        return PackageEvaluationInput(
            evaluation_key="deterministic-v1",
            score=round(max(0.0, min(100.0, score)), 3),
            semantic_score=bounded_semantic,
            reason="; ".join(reasons) if reasons else "meets deterministic package constraints",
            provenance={"evaluator": "deterministic-packages-v1"},
        )

    @staticmethod
    def select(
        evaluated: list[tuple[str, PackageEvaluationInput]]
    ) -> PackageSelection:
        if not evaluated:
            raise ValueError("at least one evaluated package is required")
        package_id, evaluation = sorted(
            evaluated,
            key=lambda item: (-item[1].score, item[0]),
        )[0]
        return PackageSelection(
            package_id=package_id,
            reason=(
                f"highest deterministic score {evaluation.score:.3f}; "
                f"{evaluation.reason}"
            ),
        )


def _fingerprint(content: dict[str, Any], *, keys: tuple[str, ...]) -> str:
    normalized = {
        key: re.sub(r"\s+", " ", str(content[key]).lower()).strip() for key in keys
    }
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()[:32]


def _bounded_semantic_score(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, float(value)))
