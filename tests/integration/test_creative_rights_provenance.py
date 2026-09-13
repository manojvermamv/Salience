"""PostgreSQL-backed release-gate contracts for creative rights and provenance."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from salience.creative.repository import CreativeRepository
from salience.creative.service import ApprovedProduction, CreativeGovernanceDenied, CreativeService


async def _production_fixture() -> tuple[CreativeRepository, ApprovedProduction]:
    from test_creative_repository import _seed_brief

    seeded = await _seed_brief()
    repository = CreativeRepository(os.environ["TEST_DATABASE_URL"])
    script_id = await repository.record_script(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_key="rights-provenance-script",
        version=1,
        status="approved",
        target_format="short_video",
        target_duration_seconds=30,
        script={"sections": [{"text": "Rights-linked fixture media"}]},
        claim_ids=[seeded["claim_id"]],
        evidence_ids=[seeded["evidence_id"]],
        trace_id="rights-provenance-script",
    )
    creative_job_id = await repository.record_creative_job(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        requested_capability="text_to_video",
        request_fingerprint="rights-provenance-request",
        idempotency_key="rights-provenance-request",
        request={"capability": "text_to_video"},
        timeout_seconds=60,
        trace_id="rights-provenance-job",
    )
    asset = await repository.record_asset_variant(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        creative_job_id=creative_job_id,
        storage_key="creative/rights-provenance.mp4",
        content_hash="f" * 64,
        media_type="video/mp4",
        byte_size=512,
        origin_type="generated",
        variant_key="primary",
        selection_state="selected",
        selection_reason="fixture",
        trace_id="rights-provenance-asset",
    )
    return repository, ApprovedProduction(
        workspace_id=seeded["workspace_id"],
        content_program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        asset_id=asset.asset_id,
        profile_key="rights-provenance",
        profile_version=1,
        profile_rules={
            "target_platform": "fixture-platform",
            "aspect_ratio": "9:16",
            "caption_limit": 100,
            "allowed_locales": ["en"],
            "requires_disclosure": True,
        },
        title_candidates=[
            {
                "key": "primary",
                "title": "Rights-linked fixture media",
                "thumbnail_asset_id": asset.asset_id,
                "narrative_fingerprint": script_id,
                "thumbnail_fingerprint": asset.asset_id,
                "template_key": "rights-provenance",
            }
        ],
        selected_title_key="primary",
        caption="Rights-linked fixture media.",
        aspect_ratio="9:16",
        duration_seconds=30,
        locale="en",
        claim_ids=[seeded["claim_id"]],
        generated=True,
        approval_state="approved",
    )


@pytest.mark.asyncio
async def test_finalization_loads_likeness_consent_from_canonical_asset_link() -> None:
    repository, production = await _production_fixture()
    consent_id, likeness_id = await asyncio.to_thread(
        _insert_active_likeness_consent,
        os.environ["TEST_DATABASE_URL"],
        production.workspace_id,
    )
    await repository.record_asset_rights_link(
        asset_id=production.asset_id,
        link_key="subject-likeness",
        relation="likeness",
        reference_id=likeness_id,
    )
    canonical = production.__class__(
        **{
            **production.__dict__,
            "uses_real_likeness": True,
            "consent": None,
        }
    )

    distribution = await CreativeService(repository).build_distribution(canonical)
    ready = await CreativeService(repository).finalize_ready_package(canonical, distribution)

    assert consent_id
    assert ready.approval_state == "approved"


@pytest.mark.asyncio
async def test_finalization_rejects_persisted_required_c2pa_not_configured() -> None:
    repository, production = await _production_fixture()
    await repository.record_asset_provenance(
        asset_id=production.asset_id,
        origin_type="generated",
        validation_status="not_configured",
        c2pa_manifest_reference=None,
        signer_metadata={},
    )
    c2pa_required = production.__class__(
        **{
            **production.__dict__,
            "profile_rules": {**production.profile_rules, "requires_c2pa": True},
            "c2pa_status": "valid",
        }
    )

    with pytest.raises(CreativeGovernanceDenied, match="c2pa_required"):
        await CreativeService(repository).finalize_ready_package(c2pa_required)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocker", "expected"),
    [
        ("consent_revoked", "consent_revoked"),
        ("consent_expired", "consent_expired"),
        ("territory_not_permitted", "territory_not_permitted"),
        ("voice_consent_missing", "voice_consent_missing"),
        ("license_expired", "license_expired"),
        ("channel_restricted", "channel_not_permitted"),
    ],
)
async def test_distribution_rejects_persisted_rights_blockers(
    blocker: str, expected: str
) -> None:
    repository, production = await _production_fixture()
    overrides: dict[str, object] = {}
    if blocker in {"consent_revoked", "consent_expired", "territory_not_permitted"}:
        consent_id = await asyncio.to_thread(
            _insert_direct_consent,
            os.environ["TEST_DATABASE_URL"],
            production.workspace_id,
            blocker,
        )
        await repository.record_asset_rights_link(
            asset_id=production.asset_id,
            link_key="direct-consent",
            relation="consent",
            reference_id=consent_id,
        )
        overrides["uses_real_likeness"] = True
    elif blocker == "voice_consent_missing":
        overrides["uses_voice_clone"] = True
    elif blocker == "license_expired":
        license_id = await asyncio.to_thread(
            _insert_expired_license, os.environ["TEST_DATABASE_URL"], production.asset_id
        )
        await repository.record_asset_rights_link(
            asset_id=production.asset_id,
            link_key="expired-license",
            relation="asset_license",
            reference_id=license_id,
        )
    elif blocker == "channel_restricted":
        restriction_id = await asyncio.to_thread(
            _insert_channel_restriction, os.environ["TEST_DATABASE_URL"], production.asset_id
        )
        await repository.record_asset_rights_link(
            asset_id=production.asset_id,
            link_key="channel-restriction",
            relation="usage_restriction",
            reference_id=restriction_id,
        )
    else:
        raise AssertionError(f"unexpected blocker fixture: {blocker}")
    governed = production.__class__(**{**production.__dict__, **overrides})

    with pytest.raises(CreativeGovernanceDenied, match=expected):
        await CreativeService(repository).build_distribution(governed)


def _insert_active_likeness_consent(database_url: str, workspace_id: str) -> tuple[str, str]:
    expires_at = datetime.now(UTC) + timedelta(days=1)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO consent_records (
                workspace_id, subject_type, subject_id, status, permitted_channels,
                commercial_use, territories, expires_at, evidence
            ) VALUES (%s, 'likeness', 'fixture-person', 'active', %s::jsonb, true, %s::jsonb, %s, '{}'::jsonb)
            RETURNING id::text
            """,
            (workspace_id, '["fixture-platform"]', '["US"]', expires_at),
        )
        consent_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO likeness_identities (workspace_id, identity_key, consent_record_id, status)
            VALUES (%s, 'fixture-person', %s, 'active')
            RETURNING id::text
            """,
            (workspace_id, consent_id),
        )
        return consent_id, cursor.fetchone()[0]


def _insert_direct_consent(database_url: str, workspace_id: str, blocker: str) -> str:
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=1)
    revoked_at = None
    territories = '["US"]'
    if blocker == "consent_revoked":
        revoked_at = now
    elif blocker == "consent_expired":
        expires_at = now - timedelta(seconds=1)
    elif blocker == "territory_not_permitted":
        territories = '["CA"]'
    else:
        raise AssertionError(f"unexpected consent blocker: {blocker}")
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO consent_records (
                workspace_id, subject_type, subject_id, status, permitted_channels,
                commercial_use, territories, expires_at, revoked_at, evidence
            ) VALUES (%s, %s, %s, 'active', '["fixture-platform"]'::jsonb, false, %s::jsonb, %s, %s, '{}'::jsonb)
            RETURNING id::text
            """,
            (workspace_id, blocker, blocker, territories, expires_at, revoked_at),
        )
        return cursor.fetchone()[0]


def _insert_expired_license(database_url: str, asset_id: str) -> str:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO asset_licenses (
                asset_id, license_type, commercial_use, terms, expires_at, status
            ) VALUES (%s, 'fixture', false, '{}'::jsonb, %s, 'active')
            RETURNING id::text
            """,
            (asset_id, datetime.now(UTC) - timedelta(seconds=1)),
        )
        return cursor.fetchone()[0]


def _insert_channel_restriction(database_url: str, asset_id: str) -> str:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO usage_restrictions (asset_id, restriction_type, document, status)
            VALUES (%s, 'channel', '{"prohibited_channels": ["fixture-platform"]}'::jsonb, 'active')
            RETURNING id::text
            """,
            (asset_id,),
        )
        return cursor.fetchone()[0]
