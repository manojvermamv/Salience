import pytest

from salience.creative.service import CreativeGovernanceDenied, CreativeService
from salience.creative.service import ApprovedProduction


class RecordingRepository:
    async def record_platform_profile(self, **kwargs: object) -> str:
        return "profile-1"

    async def record_distribution_package(self, **kwargs: object) -> str:
        return "distribution-1"

    async def record_title_thumbnail_candidate(self, **kwargs: object) -> str:
        return "candidate-1"

    async def record_localization(self, **kwargs: object) -> str:
        return "localization-1"

    async def record_originality_evaluation(self, **kwargs: object) -> str:
        return "originality-1"

    async def record_synthetic_media_disclosure(self, **kwargs: object) -> str:
        return "disclosure-1"


def _approved_production(**overrides: object) -> ApprovedProduction:
    values: dict[str, object] = {
        "workspace_id": "workspace-1",
        "content_program_id": "program-1",
        "brief_id": "brief-1",
        "script_id": "script-1",
        "asset_id": "asset-1",
        "profile_key": "short-video",
        "profile_version": 1,
        "profile_rules": {
            "aspect_ratio": "9:16",
            "caption_limit": 100,
            "allowed_locales": ["en", "fr"],
            "requires_disclosure": True,
        },
        "title_candidates": [{"key": "primary", "title": "Evidence-linked garden care"}],
        "selected_title_key": "primary",
        "caption": "Evidence-linked garden care.",
        "aspect_ratio": "9:16",
        "duration_seconds": 30,
        "locale": "en",
        "claim_ids": ["claim-1"],
        "generated": True,
        "approval_state": "approved",
    }
    values.update(overrides)
    return ApprovedProduction(**values)


@pytest.mark.asyncio
async def test_finalization_rejects_localization_claim_injection_and_publish_metadata() -> None:
    service = CreativeService(RecordingRepository())

    with pytest.raises(CreativeGovernanceDenied, match="localization_claim_injection"):
        await service.build_distribution(
            _approved_production(
                localizations=[
                    {"target_locale": "fr", "content": {"title": "Garden"}, "claim_ids": ["injected"]}
                ]
            )
        )
    with pytest.raises(CreativeGovernanceDenied, match="publication_metadata_forbidden"):
        await service.build_distribution(
            _approved_production(package_metadata={"platform_account_id": "do-not-store"})
        )


@pytest.mark.asyncio
async def test_finalization_rejects_required_c2pa_that_is_not_configured() -> None:
    service = CreativeService(RecordingRepository())

    with pytest.raises(CreativeGovernanceDenied, match="c2pa_required"):
        await service.finalize_ready_package(
            _approved_production(
                profile_rules={
                    **_approved_production().profile_rules,
                    "requires_c2pa": True,
                },
                c2pa_status="not_configured",
            )
        )
