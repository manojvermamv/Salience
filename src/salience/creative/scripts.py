"""Deterministic validation for claim-preserving script revisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class ScriptVerification:
    allowed: bool
    blocker_codes: tuple[str, ...]
    warning_codes: tuple[str, ...] = ()


class ScriptVerifier:
    """Validate structural and evidence-linked script invariants without a model."""

    _misleading_patterns = (
        "guaranteed",
        "miracle",
        "never believe",
        "secret cure",
        "risk free",
    )

    def evaluate(
        self, script: Mapping[str, Any], brief: Mapping[str, Any]
    ) -> ScriptVerification:
        blockers: list[str] = []
        warnings: list[str] = []
        if script.get("brief_id") != brief.get("brief_id"):
            blockers.append("brief_identity_mismatch")

        script_claim_ids = _string_set(script.get("claim_ids"))
        brief_claim_ids = _string_set(brief.get("claim_ids"))
        if not script_claim_ids:
            blockers.append("missing_claim_links")
        if not script_claim_ids <= brief_claim_ids:
            blockers.append("unsupported_claim")

        script_evidence_ids = _string_set(script.get("evidence_ids"))
        brief_evidence_ids = _string_set(brief.get("evidence_ids"))
        if not script_evidence_ids:
            blockers.append("missing_evidence_links")
        elif brief_evidence_ids and not script_evidence_ids <= brief_evidence_ids:
            blockers.append("unsupported_evidence")

        contradicted_claims = _string_set(script.get("contradiction_markers"))
        if contradicted_claims & script_claim_ids:
            blockers.append("contradicted_claim")

        sections = script.get("sections")
        if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
            blockers.append("missing_sections")
            sections = []
        section_texts: list[str] = []
        duration_total = 0
        cta_present = False
        for section in sections:
            if not isinstance(section, Mapping):
                blockers.append("invalid_section")
                continue
            text = section.get("text")
            if not isinstance(text, str) or not text.strip():
                blockers.append("empty_section")
                continue
            section_texts.append(_normalize_text(text))
            duration = section.get("duration_seconds")
            if not isinstance(duration, int) or duration <= 0:
                blockers.append("invalid_section_duration")
            else:
                duration_total += duration
            if section.get("kind") == "cta":
                cta_present = True

        if len(section_texts) != len(set(section_texts)):
            blockers.append("repeated_section")
        normalized_script = " ".join(section_texts)
        if any(pattern in normalized_script for pattern in self._misleading_patterns):
            blockers.append("misleading_framing")

        target_duration = script.get("target_duration_seconds")
        if not isinstance(target_duration, int) or target_duration <= 0:
            blockers.append("invalid_target_duration")
        elif duration_total != target_duration:
            blockers.append("duration_mismatch")
        if cta_present and not _brief_content(brief).get("cta_allowed", True):
            blockers.append("cta_not_allowed")
        if not cta_present:
            warnings.append("missing_cta")
        return ScriptVerification(
            allowed=not blockers,
            blocker_codes=tuple(sorted(set(blockers))),
            warning_codes=tuple(sorted(set(warnings))),
        )


def _brief_content(brief: Mapping[str, Any]) -> Mapping[str, Any]:
    content = brief.get("content")
    return content if isinstance(content, Mapping) else {}


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()
