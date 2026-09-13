"""Deterministic rights, disclosure, platform, and originality gates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    blocker_codes: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True)
class DisclosureDecision:
    required: bool
    labels: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class OriginalityDecision:
    allowed: bool
    blocker_codes: tuple[str, ...]
    metrics: dict[str, float]


class RightsPolicy:
    """Fail closed for consent-controlled likeness and cloned-voice use."""

    def authorize(
        self,
        *,
        uses_real_likeness: bool = False,
        uses_voice_clone: bool = False,
        consent: Mapping[str, Any] | None,
        channel: str,
        territory: str,
        commercial_use: bool,
        now: datetime | None = None,
    ) -> GateDecision:
        if not uses_real_likeness and not uses_voice_clone:
            return GateDecision(True, ())
        if consent is None:
            return GateDecision(False, ("missing_consent",), "missing_consent")
        current_time = now or datetime.now(UTC)
        blockers: list[str] = []
        if consent.get("status") != "active":
            blockers.append("consent_inactive")
        if _has_time_passed(consent.get("revoked_at"), current_time):
            blockers.append("consent_revoked")
        expiry = _parse_time(consent.get("expires_at"))
        if expiry is None:
            blockers.append("consent_expiry_missing")
        elif expiry <= current_time:
            blockers.append("consent_expired")
        if channel not in _string_set(consent.get("permitted_channels")):
            blockers.append("channel_not_permitted")
        if territory not in _string_set(consent.get("territories")):
            blockers.append("territory_not_permitted")
        if commercial_use and consent.get("commercial_use") is not True:
            blockers.append("commercial_use_not_permitted")
        codes = tuple(sorted(set(blockers)))
        return GateDecision(not codes, codes, codes[0] if codes else None)


class DisclosurePolicy:
    """Derive a disclosure decision from governed media facts, never a provider claim."""

    def decide(self, media: Mapping[str, Any]) -> DisclosureDecision:
        generated = media.get("generated") is True
        altered = media.get("altered") is True
        realistic = media.get("realistic") is True
        likeness = media.get("uses_real_likeness") is True
        voice_clone = media.get("uses_voice_clone") is True
        profile_required = media.get("profile_requires_disclosure") is True
        jurisdiction_required = media.get("jurisdiction_requires_disclosure") is True
        required = any(
            (
                generated,
                altered,
                realistic,
                likeness,
                voice_clone,
                profile_required,
                jurisdiction_required,
            )
        )
        labels: list[str] = []
        if generated or altered:
            labels.append("synthetic_media")
        if realistic and likeness:
            labels.append("realistic_likeness")
        if voice_clone:
            labels.append("voice_clone")
        if media.get("c2pa_status") in {"not_configured", "invalid", "missing"}:
            labels.append("c2pa_unavailable")
        return DisclosureDecision(
            required=required,
            labels=tuple(sorted(set(labels))),
            status="required" if required else "not_required",
        )


class PlatformValidator:
    """Validate only the supplied immutable platform-profile rules."""

    def validate(
        self, package: Mapping[str, Any], profile: Mapping[str, Any]
    ) -> GateDecision:
        blockers: list[str] = []
        expected_aspect_ratio = profile.get("aspect_ratio")
        if expected_aspect_ratio and package.get("aspect_ratio") != expected_aspect_ratio:
            blockers.append("aspect_ratio")
        caption_limit = profile.get("caption_limit")
        caption = package.get("caption", "")
        if isinstance(caption_limit, int) and (
            not isinstance(caption, str) or len(caption) > caption_limit
        ):
            blockers.append("caption_limit")
        max_duration = profile.get("max_duration_seconds")
        if isinstance(max_duration, int) and (
            not isinstance(package.get("duration_seconds"), int)
            or package["duration_seconds"] > max_duration
        ):
            blockers.append("duration_limit")
        allowed_locales = _string_set(profile.get("allowed_locales"))
        if allowed_locales and package.get("locale") not in allowed_locales:
            blockers.append("locale_not_allowed")
        requires_disclosure = (
            profile.get("requires_disclosure") is True
            or profile.get("required_disclosure") is True
        )
        if requires_disclosure and not package.get("disclosure"):
            blockers.append("missing_disclosure")
        codes = tuple(sorted(set(blockers)))
        return GateDecision(not codes, codes, codes[0] if codes else None)


class OriginalityValidator:
    """Reject exact repeat fingerprints while exposing deterministic similarity metrics."""

    def evaluate(
        self, candidate: Mapping[str, Any], history: Sequence[Mapping[str, Any]]
    ) -> OriginalityDecision:
        candidate_title = _normalize_text(candidate.get("title"))
        candidate_narrative = candidate.get("narrative_fingerprint")
        candidate_thumbnail = candidate.get("thumbnail_fingerprint")
        candidate_template = candidate.get("template_key")
        blockers: list[str] = []
        metrics = {
            "title_match_count": 0.0,
            "narrative_match_count": 0.0,
            "thumbnail_match_count": 0.0,
            "template_match_count": 0.0,
        }
        for previous in history:
            if candidate_title and candidate_title == _normalize_text(previous.get("title")):
                blockers.append("duplicate_title")
                metrics["title_match_count"] += 1
            if candidate_narrative and candidate_narrative == previous.get("narrative_fingerprint"):
                blockers.append("duplicate_narrative")
                metrics["narrative_match_count"] += 1
            if candidate_thumbnail and candidate_thumbnail == previous.get("thumbnail_fingerprint"):
                blockers.append("duplicate_thumbnail")
                metrics["thumbnail_match_count"] += 1
            if candidate_template and candidate_template == previous.get("template_key"):
                metrics["template_match_count"] += 1
        codes = tuple(sorted(set(blockers)))
        return OriginalityDecision(not codes, codes, metrics)


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _has_time_passed(value: object, now: datetime) -> bool:
    parsed = _parse_time(value)
    return parsed is not None and parsed <= now


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.casefold()).strip()
