"""Explainable Phase 5 opportunity scoring with bounded semantic adjustment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from salience.intelligence.signals import CanonicalSignal, SIGNAL_FEATURES


WEIGHTS = {
    "freshness": 0.18,
    "velocity": 0.14,
    "source_diversity": 0.14,
    "niche_relevance": 0.20,
    "audience_relevance": 0.14,
    "novelty": 0.12,
    "confidence": 0.08,
    "saturation": -0.12,
    "risk": -0.12,
}


@dataclass(frozen=True)
class RankedOpportunity:
    fingerprint: str
    topic: str
    score: float
    base_score: float
    semantic_adjustment: float
    features: dict[str, float]
    feature_availability: dict[str, list[str]]
    explanation: str
    risks: list[str]
    signal_ids: list[str]


class OpportunityRanker:
    def rank(
        self,
        signals: list[CanonicalSignal],
        model_judgements: list[dict[str, Any]],
    ) -> list[RankedOpportunity]:
        adjustments = {
            str(judgement.get("fingerprint")): _bounded_adjustment(judgement.get("adjustment", 0))
            for judgement in model_judgements
            if isinstance(judgement, dict)
        }
        explanations = {
            str(judgement.get("fingerprint")): str(judgement.get("explanation", ""))
            for judgement in model_judgements
            if isinstance(judgement, dict)
        }
        ranked = [
            self._rank(signal, adjustments.get(signal.fingerprint, 0), explanations.get(signal.fingerprint, ""))
            for signal in signals
        ]
        return sorted(ranked, key=lambda opportunity: (-opportunity.score, opportunity.fingerprint))

    @staticmethod
    def _rank(
        signal: CanonicalSignal, adjustment: float, semantic_explanation: str
    ) -> RankedOpportunity:
        weighted = sum(
            WEIGHTS[name] * signal.features.get(name, 0.0) for name in WEIGHTS
        )
        base_score = round(100 * _clamp(weighted), 3)
        score = round(_clamp((base_score + adjustment) / 100) * 100, 3)
        missing = sorted(set(SIGNAL_FEATURES) - set(signal.features))
        availability = {
            "present": sorted(signal.features),
            "missing": missing,
        }
        detail = f"deterministic base score {base_score:.3f}"
        if adjustment:
            detail = f"{detail}; bounded semantic adjustment {adjustment:+.3f}"
        if semantic_explanation:
            detail = f"{detail}; model rationale: {semantic_explanation}"
        return RankedOpportunity(
            fingerprint=signal.fingerprint,
            topic=signal.topic,
            score=score,
            base_score=base_score,
            semantic_adjustment=adjustment,
            features=dict(signal.features),
            feature_availability=availability,
            explanation=detail,
            risks=["untrusted_external_support"]
            if signal.supports
            else ["missing_source_support"],
            signal_ids=[signal.fingerprint],
        )


def _bounded_adjustment(value: object) -> float:
    if not isinstance(value, int | float):
        return 0
    return max(-10.0, min(10.0, float(value)))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
