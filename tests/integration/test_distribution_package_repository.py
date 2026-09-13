import pytest

from salience.creative.service import (
    ApprovedProduction,
    CreativeGovernanceDenied,
    CreativeService,
)


class RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def record_platform_profile(self, **kwargs: object) -> str:
        self.calls.append(("profile", kwargs))
        return "profile-1"

    async def record_distribution_package(self, **kwargs: object) -> str:
        self.calls.append(("distribution", kwargs))
        return "distribution-1"

    async def record_title_thumbnail_candidate(self, **kwargs: object) -> str:
        self.calls.append(("candidate", kwargs))
        return "candidate-1"

    async def record_localization(self, **kwargs: object) -> str:
        self.calls.append(("localization", kwargs))
        return "localization-1"

    async def record_originality_evaluation(self, **kwargs: object) -> str:
        self.calls.append(("originality", kwargs))
        return "originality-1"

    async def record_synthetic_media_disclosure(self, **kwargs: object) -> str:
        self.calls.append(("disclosure", kwargs))
        return "disclosure-1"

    async def record_ready_package(self, **kwargs: object) -> str:
        self.calls.append(("ready", kwargs))
        return "ready-1"


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
        "title_candidates": [
            {"key": "primary", "title": "Evidence-linked garden care", "thumbnail_asset_id": "asset-1"}
        ],
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
async def test_finalization_emits_an_immutable_ready_package() -> None:
    repository = RecordingRepository()
    service = CreativeService(repository)

    ready = await service.finalize_ready_package(_approved_production())

    assert ready.contract_version == "ReadyToPublishPackage@v1"
    assert ready.approval_state == "approved"
    assert ready.disclosure_decision_id == "disclosure-1"
    assert ready.platform_profile_id == "profile-1"
    assert [name for name, _ in repository.calls][-1] == "ready"


@pytest.mark.asyncio
async def test_finalization_refuses_missing_likeness_consent() -> None:
    service = CreativeService(RecordingRepository())

    with pytest.raises(CreativeGovernanceDenied, match="missing_consent"):
        await service.finalize_ready_package(
            _approved_production(uses_real_likeness=True, consent=None)
        )


@pytest.mark.asyncio
async def test_distribution_records_selected_and_rejected_title_candidate_reasons() -> None:
    repository = RecordingRepository()
    service = CreativeService(repository)

    await service.build_distribution(
        _approved_production(
            title_candidates=[
                {"key": "primary", "title": "Evidence-linked garden care"},
                {"key": "alternate", "title": "Garden care evidence"},
            ]
        )
    )

    candidate_calls = [kwargs for name, kwargs in repository.calls if name == "candidate"]
    assert candidate_calls == [
        {
            "distribution_package_id": "distribution-1",
            "candidate_key": "primary",
            "title": "Evidence-linked garden care",
            "thumbnail_asset_id": None,
            "selection_state": "selected",
            "reason": "selected",
            "score": 0.0,
        },
        {
            "distribution_package_id": "distribution-1",
            "candidate_key": "alternate",
            "title": "Garden care evidence",
            "thumbnail_asset_id": None,
            "selection_state": "rejected",
            "reason": "not_selected",
            "score": 0.0,
        },
    ]
