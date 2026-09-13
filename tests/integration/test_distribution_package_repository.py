"""Immutable distribution revision contracts backed by PostgreSQL."""

import os
from dataclasses import replace

import pytest

from salience.creative.repository import CreativeRepository
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
async def test_changed_title_after_ready_package_creates_a_new_distribution_revision() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    approved = await _approved_ready_package()
    revision = await CreativeRepository(os.environ["TEST_DATABASE_URL"]).create_distribution_revision(
        approved["distribution_package_id"], selected_title="A revised evidence-linked title"
    )

    assert revision.distribution_package_id != approved["distribution_package_id"]
    assert revision.version == 2


@pytest.mark.asyncio
async def test_approved_decisions_replay_exactly_or_create_new_distribution_and_ready_versions() -> None:
    from test_creative_rights_provenance import _production_fixture

    repository, production = await _production_fixture()
    service = CreativeService(repository)
    original_distribution = await service.build_distribution(production)
    original_ready = await service.finalize_ready_package(production, original_distribution)

    replayed = await service.build_distribution(production)
    replayed_ready = await service.finalize_ready_package(production, replayed)
    revised = await service.build_distribution(
        replace(
            production,
            title_candidates=[
                {**production.title_candidates[0], "title": "A revised evidence-linked title"}
            ],
        )
    )
    revised_ready = await service.finalize_ready_package(
        replace(
            production,
            title_candidates=[
                {**production.title_candidates[0], "title": "A revised evidence-linked title"}
            ],
        ),
        revised,
    )

    assert replayed.distribution_package_id == original_distribution.distribution_package_id
    assert replayed_ready.ready_package_id == original_ready.ready_package_id
    assert revised.distribution_package_id != original_distribution.distribution_package_id
    assert revised.version == original_distribution.version + 1
    assert revised_ready.ready_package_id != original_ready.ready_package_id


@pytest.mark.asyncio
async def test_different_approved_decision_fingerprints_receive_distinct_revisions() -> None:
    from test_creative_rights_provenance import _production_fixture

    repository, production = await _production_fixture()
    service = CreativeService(repository)
    original = await service.build_distribution(production)
    await service.finalize_ready_package(production, original)

    first = await service.build_distribution(
        replace(production, package_metadata={"description": "first revision"})
    )
    second = await service.build_distribution(
        replace(production, package_metadata={"description": "second revision"})
    )

    assert first.version == 2
    assert second.version == 3
    assert second.distribution_package_id != first.distribution_package_id


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
