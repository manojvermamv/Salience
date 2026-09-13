"""Fail-closed selection of versioned publisher capability profiles."""

from __future__ import annotations

from collections.abc import Iterable

from salience.publication.contracts import PublicationRequest, PublisherCapabilityProfile


class PublisherCapabilityDenied(ValueError):
    pass


class PublisherRegistry:
    def __init__(self, profiles: Iterable[PublisherCapabilityProfile]) -> None:
        self._profiles = tuple(sorted(profiles, key=lambda profile: (profile.publisher_id, profile.version)))

    def resolve(
        self,
        request: PublicationRequest,
        *,
        account_type: str,
        content_type: str,
    ) -> PublisherCapabilityProfile:
        reasons: list[str] = []
        for profile in self._profiles:
            reason = self._rejection_reason(profile, request, account_type, content_type)
            if reason is None:
                return profile
            reasons.append(reason)
        detail = reasons[0] if reasons else "no publisher profile is registered"
        raise PublisherCapabilityDenied(detail)

    @staticmethod
    def _rejection_reason(
        profile: PublisherCapabilityProfile,
        request: PublicationRequest,
        account_type: str,
        content_type: str,
    ) -> str | None:
        if request.publisher_id is not None and profile.publisher_id != request.publisher_id:
            return "publisher"
        if not profile.enabled:
            return "publisher is disabled"
        if profile.contract_compatibility.get("publication") != "1.0":
            return "contract compatibility"
        if profile.platform != request.platform:
            return "platform"
        if account_type not in profile.account_types:
            return "account type"
        if profile.audit_state != "verified":
            return "audit state"
        if "publish:create" not in profile.granted_scopes:
            return "scope"
        if content_type not in profile.supported_content_types:
            return "content type"
        if request.visibility not in profile.supported_visibilities:
            return "visibility"
        if not profile.disclosure_support:
            return "disclosure"
        if request.scheduled_for is not None and not profile.scheduling_support:
            return "scheduling"
        if profile.quota_state != "available":
            return "quota"
        if profile.health_state != "healthy":
            return "health"
        return None
