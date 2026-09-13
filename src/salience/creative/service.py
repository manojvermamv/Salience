"""Provider-neutral governed assembly of Phase 8 distribution packages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from salience.creative.contracts import DistributionPackage, ReadyToPublishPackage
from salience.creative.governance import (
    DisclosurePolicy,
    OriginalityValidator,
    PlatformValidator,
    RightsPolicy,
)


class CreativeGovernanceDenied(ValueError):
    """A deterministic policy blocker prevented a package from being assembled."""


@dataclass(frozen=True)
class ApprovedProduction:
    """Validated production facts, deliberately independent of publisher accounts."""

    workspace_id: str
    content_program_id: str
    brief_id: str
    script_id: str
    asset_id: str
    profile_key: str
    profile_version: int
    profile_rules: Mapping[str, Any]
    title_candidates: Sequence[Mapping[str, Any]]
    selected_title_key: str
    caption: str
    aspect_ratio: str
    duration_seconds: int
    locale: str
    claim_ids: Sequence[str]
    generated: bool
    approval_state: str
    uses_real_likeness: bool = False
    uses_voice_clone: bool = False
    consent: Mapping[str, Any] | None = None
    territory: str = "US"
    commercial_use: bool = False
    localizations: Sequence[Mapping[str, Any]] = ()
    package_metadata: Mapping[str, Any] = field(default_factory=dict)
    prior_packages: Sequence[Mapping[str, Any]] = ()
    c2pa_status: str | None = None


class CreativeService:
    """Assemble audited distribution and ready packages without publishing anything."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository
        self._rights = RightsPolicy()
        self._disclosure = DisclosurePolicy()
        self._platform = PlatformValidator()
        self._originality = OriginalityValidator()

    async def build_distribution(self, production: ApprovedProduction) -> DistributionPackage:
        """Validate all Phase 8 gates and persist a provider-neutral package."""
        self._require_approved(production)
        self._ensure_publication_metadata_is_absent(production.package_metadata)
        self._authorize_rights(production)

        disclosure = self._disclosure.decide(
            {
                "generated": production.generated,
                "uses_real_likeness": production.uses_real_likeness,
                "uses_voice_clone": production.uses_voice_clone,
                "profile_requires_disclosure": production.profile_rules.get(
                    "requires_disclosure"
                )
                is True,
                "c2pa_status": production.c2pa_status,
            }
        )
        platform_decision = self._platform.validate(
            {
                "aspect_ratio": production.aspect_ratio,
                "caption": production.caption,
                "duration_seconds": production.duration_seconds,
                "locale": production.locale,
                "disclosure": disclosure.labels if disclosure.required else None,
            },
            production.profile_rules,
        )
        self._require_allowed(platform_decision.blocker_codes)

        selected, candidate_decisions = self._validate_candidates(production)
        self._validate_localizations(production)

        target_platform = str(
            production.profile_rules.get("target_platform", production.profile_key)
        )
        profile_id = await self._repository.record_platform_profile(
            workspace_id=production.workspace_id,
            program_id=production.content_program_id,
            profile_key=production.profile_key,
            version=production.profile_version,
            target_platform=target_platform,
            rules=dict(production.profile_rules),
            status="active",
        )
        package_id = await self._repository.record_distribution_package(
            workspace_id=production.workspace_id,
            program_id=production.content_program_id,
            brief_id=production.brief_id,
            script_id=production.script_id,
            platform_profile_id=profile_id,
            package_key=self._package_key(production),
            version=1,
            locale=production.locale,
            package_metadata=dict(production.package_metadata),
            status="validated",
            asset_ids=[production.asset_id],
            verifier_results={
                "platform": {"allowed": True},
                "disclosure": {
                    "required": disclosure.required,
                    "labels": list(disclosure.labels),
                },
            },
        )

        for candidate, allowed, reason, metrics in candidate_decisions:
            selection_state = (
                "selected"
                if candidate["key"] == selected["key"]
                else "rejected"
            )
            candidate_reason = (
                "selected"
                if selection_state == "selected"
                else (reason if not allowed else "not_selected")
            )
            await self._repository.record_title_thumbnail_candidate(
                distribution_package_id=package_id,
                candidate_key=candidate["key"],
                title=candidate["title"],
                thumbnail_asset_id=candidate.get("thumbnail_asset_id"),
                selection_state=selection_state,
                reason=candidate_reason,
                score=metrics["title_match_count"],
            )

        for localization in production.localizations:
            await self._repository.record_localization(
                distribution_package_id=package_id,
                source_locale=production.locale,
                target_locale=str(localization["target_locale"]),
                content=dict(localization["content"]),
                claim_ids=list(localization.get("claim_ids", ())),
                status="verified",
            )

        selected_decision = next(
            item for item in candidate_decisions if item[0]["key"] == selected["key"]
        )
        await self._repository.record_originality_evaluation(
            distribution_package_id=package_id,
            evaluator_version="originality@v1",
            metrics=selected_decision[3],
            status="allowed",
            reason="unique",
        )
        disclosure_id = await self._repository.record_synthetic_media_disclosure(
            distribution_package_id=package_id,
            decision={
                "required": disclosure.required,
                "labels": list(disclosure.labels),
                "status": disclosure.status,
            },
            status="approved",
        )
        return DistributionPackage(
            distribution_package_id=package_id,
            platform_profile_id=profile_id,
            disclosure_decision_id=disclosure_id,
            selected_title_key=selected["key"],
            locale=production.locale,
            asset_ids=(production.asset_id,),
        )

    async def finalize_ready_package(
        self,
        production: ApprovedProduction,
        distribution: DistributionPackage | None = None,
    ) -> ReadyToPublishPackage:
        """Create the immutable, approved handoff for a future publishing phase."""
        self._require_approved(production)
        self._ensure_publication_metadata_is_absent(production.package_metadata)
        self._authorize_rights(production)
        package = distribution or await self.build_distribution(production)
        if (
            package.locale != production.locale
            or package.asset_ids != (production.asset_id,)
        ):
            raise CreativeGovernanceDenied("distribution_mismatch")
        ready_id = await self._repository.record_ready_package(
            workspace_id=production.workspace_id,
            program_id=production.content_program_id,
            brief_id=production.brief_id,
            script_id=production.script_id,
            distribution_package_id=package.distribution_package_id,
            platform_profile_id=package.platform_profile_id,
            disclosure_id=package.disclosure_decision_id,
            ready_package_key=f"{self._package_key(production)}:ready",
            version=1,
            approval_state="approved",
            verifier_results={
                "distribution_contract": package.contract_version,
                "selected_title_key": package.selected_title_key,
            },
            lineage={
                "content_brief_id": production.brief_id,
                "script_id": production.script_id,
                "asset_ids": list(package.asset_ids),
                "claim_ids": list(production.claim_ids),
                "distribution_package_id": package.distribution_package_id,
            },
        )
        return ReadyToPublishPackage(
            ready_package_id=ready_id,
            distribution_package_id=package.distribution_package_id,
            platform_profile_id=package.platform_profile_id,
            disclosure_decision_id=package.disclosure_decision_id,
            approval_state="approved",
        )

    def _authorize_rights(self, production: ApprovedProduction) -> None:
        decision = self._rights.authorize(
            uses_real_likeness=production.uses_real_likeness,
            uses_voice_clone=production.uses_voice_clone,
            consent=production.consent,
            channel=str(production.profile_rules.get("target_platform", production.profile_key)),
            territory=production.territory,
            commercial_use=production.commercial_use,
        )
        self._require_allowed(decision.blocker_codes)

    def _validate_candidates(
        self, production: ApprovedProduction
    ) -> tuple[dict[str, Any], list[tuple[dict[str, Any], bool, str, dict[str, float]]]]:
        if not production.title_candidates:
            raise CreativeGovernanceDenied("title_candidate_missing")
        decisions: list[tuple[dict[str, Any], bool, str, dict[str, float]]] = []
        selected: dict[str, Any] | None = None
        for raw_candidate in production.title_candidates:
            candidate = dict(raw_candidate)
            key = candidate.get("key")
            title = candidate.get("title")
            if not isinstance(key, str) or not key or not isinstance(title, str) or not title.strip():
                raise CreativeGovernanceDenied("title_candidate_invalid")
            decision = self._originality.evaluate(candidate, production.prior_packages)
            reason = "unique" if decision.allowed else decision.blocker_codes[0]
            decisions.append((candidate, decision.allowed, reason, decision.metrics))
            if key == production.selected_title_key:
                selected = candidate
                if not decision.allowed:
                    self._require_allowed(decision.blocker_codes)
        if selected is None:
            raise CreativeGovernanceDenied("selected_title_missing")
        return selected, decisions

    def _validate_localizations(self, production: ApprovedProduction) -> None:
        allowed_locales = production.profile_rules.get("allowed_locales")
        allowed = {
            locale
            for locale in allowed_locales
            if isinstance(locale, str) and locale
        } if isinstance(allowed_locales, Sequence) and not isinstance(allowed_locales, str) else set()
        known_claims = set(production.claim_ids)
        for localization in production.localizations:
            target = localization.get("target_locale")
            content = localization.get("content")
            claims = localization.get("claim_ids", ())
            if not isinstance(target, str) or not target or not isinstance(content, Mapping):
                raise CreativeGovernanceDenied("localization_invalid")
            if allowed and target not in allowed:
                raise CreativeGovernanceDenied("localization_locale_not_allowed")
            if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
                raise CreativeGovernanceDenied("localization_claim_injection")
            if any(not isinstance(claim, str) or claim not in known_claims for claim in claims):
                raise CreativeGovernanceDenied("localization_claim_injection")

    @staticmethod
    def _require_approved(production: ApprovedProduction) -> None:
        if production.approval_state != "approved":
            raise CreativeGovernanceDenied("approval_required")

    @staticmethod
    def _require_allowed(blockers: Sequence[str]) -> None:
        if blockers:
            raise CreativeGovernanceDenied(blockers[0])

    @classmethod
    def _ensure_publication_metadata_is_absent(cls, value: Mapping[str, Any]) -> None:
        if cls._contains_publication_metadata(value):
            raise CreativeGovernanceDenied("publication_metadata_forbidden")

    @classmethod
    def _contains_publication_metadata(cls, value: object) -> bool:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if isinstance(key, str) and cls._forbidden_metadata_key(key):
                    return True
                if cls._contains_publication_metadata(nested):
                    return True
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return any(cls._contains_publication_metadata(item) for item in value)
        return False

    @staticmethod
    def _forbidden_metadata_key(key: str) -> bool:
        normalized = key.casefold().replace("-", "_")
        return (
            normalized in {
                "access_token",
                "api_key",
                "authorization",
                "credential",
                "credentials",
                "platform_account_id",
                "publish_payload",
            }
            or normalized.startswith("publish_")
            or normalized.endswith("_account_id")
        )

    @staticmethod
    def _package_key(production: ApprovedProduction) -> str:
        return f"{production.script_id}:{production.profile_key}:{production.locale}"
