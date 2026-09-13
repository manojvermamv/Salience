"""Fail-closed authorization immediately before a publication effect."""

from __future__ import annotations

from dataclasses import dataclass

from salience.publication.contracts import PublicationRequest, PublisherCapabilityProfile
from salience.publication.registry import PublisherCapabilityDenied, PublisherRegistry


@dataclass(frozen=True)
class PublicationAuthorizationContext:
    request: PublicationRequest
    profile: PublisherCapabilityProfile
    ready_package_id: str
    ready_package_workspace_id: str
    ready_package_program_id: str
    ready_package_approval_state: str
    account_workspace_id: str
    connection_account_id: str
    account_type: str
    account_status: str
    connection_status: str
    connection_scopes: frozenset[str]
    content_type: str
    authorized_destination: str
    authorized_locale: str
    authorized_territory: str
    authorized_visibility: str
    policy_allowed: bool
    rights_allowed: bool
    disclosure_allowed: bool
    publishing_approval_state: str
    budget_status: str
    rate_quota_available: bool
    capability_profile_version: int


@dataclass(frozen=True)
class PublicationAuthorization:
    allowed: bool
    reasons: tuple[str, ...]


class PublicationAuthorizer:
    """Evaluate only canonical, current facts; absent facts deny publication."""

    async def reauthorize(
        self, context: PublicationAuthorizationContext
    ) -> PublicationAuthorization:
        request = context.request
        reasons: list[str] = []
        if context.ready_package_id != request.ready_package_id:
            reasons.append("ready_package")
        if context.ready_package_workspace_id != request.workspace_id:
            reasons.append("ready_package_workspace")
        if context.ready_package_program_id != request.content_program_id:
            reasons.append("ready_package_program")
        if context.ready_package_approval_state != "approved":
            reasons.append("ready_package_approval")
        if context.account_workspace_id != request.workspace_id:
            reasons.append("account_workspace")
        if context.account_status != "active":
            reasons.append("account_status")
        if context.connection_account_id != request.publisher_account_id:
            reasons.append("connection_account")
        if context.connection_status != "active":
            reasons.append("connection_status")
        if "publish:create" not in context.connection_scopes:
            reasons.append("connection_scope")
        if context.capability_profile_version != request.capability_profile_version:
            reasons.append("capability_profile_version")
        if context.authorized_destination != request.destination:
            reasons.append("destination")
        if context.authorized_locale != request.locale:
            reasons.append("locale")
        if context.authorized_territory != request.territory:
            reasons.append("territory")
        if context.authorized_visibility != request.visibility:
            reasons.append("visibility")
        try:
            PublisherRegistry((context.profile,)).resolve(
                request,
                account_type=context.account_type,
                content_type=context.content_type,
            )
        except PublisherCapabilityDenied as error:
            reasons.append(f"capability:{error}")
        if not context.policy_allowed:
            reasons.append("policy")
        if not context.rights_allowed:
            reasons.append("rights")
        if not context.disclosure_allowed:
            reasons.append("disclosure")
        if context.publishing_approval_state != "approved":
            reasons.append("publishing_approval")
        if context.budget_status != "reserved":
            reasons.append("budget")
        if not context.rate_quota_available:
            reasons.append("rate_quota")
        return PublicationAuthorization(allowed=not reasons, reasons=tuple(reasons))
