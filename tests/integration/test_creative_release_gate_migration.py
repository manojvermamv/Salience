"""Direct PostgreSQL contracts for the Phase 7-8 release-gate migration."""

import asyncio
import json
import os

import psycopg
import pytest

from salience.creative.repository import CreativeRepository


def _unique_columns(connection: psycopg.Connection, table_name: str) -> set[tuple[str, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT array_agg(attribute.attname ORDER BY key_columns.ordinality)
            FROM pg_constraint constraint_entry
            JOIN pg_class relation ON relation.oid = constraint_entry.conrelid
            JOIN unnest(constraint_entry.conkey) WITH ORDINALITY AS key_columns(attribute_number, ordinality)
                ON TRUE
            JOIN pg_attribute attribute
                ON attribute.attrelid = relation.oid
               AND attribute.attnum = key_columns.attribute_number
            WHERE relation.relname = %s AND constraint_entry.contype = 'u'
            GROUP BY constraint_entry.oid
            """,
            (table_name,),
        )
        return {tuple(columns) for (columns,) in cursor.fetchall()}


def test_release_gate_migration_has_canonical_effect_and_webhook_uniqueness() -> None:
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        assert _unique_columns(connection, "creative_provider_webhook_receipts") == {
            ("provider_id", "delivery_identity"),
            ("provider_job_id", "safe_payload_hash"),
        }


async def _approved_ready_package(
    *,
    publication_proofs: bool = True,
    publication_policy_document: dict[str, object] | None = None,
    asset_license_terms: dict[str, object] | None = None,
) -> dict[str, str]:
    from test_creative_repository import _seed_brief

    if publication_policy_document is None:
        publication_policy_document = {
            "publication_scope": {
                "platform": "fixture",
                "destination": "fixture://account",
                "locale": "en",
                "territory": "global",
                "visibility": "private",
            }
        }
    if asset_license_terms is None:
        asset_license_terms = {
            "permitted_channels": ["fixture"],
            "territories": ["global"],
        }

    seeded = await _seed_brief()
    repository = CreativeRepository(os.environ["TEST_DATABASE_URL"])
    script_id = await repository.record_script(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_key="release-gate-script",
        version=1,
        status="approved",
        target_format="short_video",
        target_duration_seconds=30,
        script={"sections": [{"text": "Release-gate evidence"}]},
        claim_ids=[seeded["claim_id"]],
        evidence_ids=[seeded["evidence_id"]],
        trace_id="trace-release-gate-script",
    )
    creative_job_id = await repository.record_creative_job(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        requested_capability="text_to_video",
        request_fingerprint="release-gate-request-fingerprint",
        idempotency_key="release-gate-request-key",
        request={"prompt": "Release-gate evidence", "duration_seconds": 30},
        timeout_seconds=60,
        trace_id="trace-release-gate-job",
    )
    asset = await repository.record_asset_variant(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        creative_job_id=creative_job_id,
        storage_key="creative/release-gate.mp4",
        content_hash="e" * 64,
        media_type="video/mp4",
        byte_size=512,
        origin_type="generated",
        variant_key="candidate-1",
        selection_state="selected",
        selection_reason="deterministic fixture",
        trace_id="trace-release-gate-asset",
    )
    profile_id = await repository.record_platform_profile(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        profile_key="release-gate-profile",
        version=1,
        target_platform="fixture-platform",
        rules={"aspect_ratio": "9:16"},
        status="active",
    )
    distribution_package_id = await repository.record_distribution_package(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        platform_profile_id=profile_id,
        package_key="release-gate-package",
        version=1,
        locale="en",
        package_metadata={"title": "Release-gate evidence"},
        status="validated",
        asset_ids=[asset.asset_id],
    )
    candidate_id = await repository.record_title_thumbnail_candidate(
        distribution_package_id=distribution_package_id,
        candidate_key="primary",
        title="Release-gate evidence",
        thumbnail_asset_id=asset.asset_id,
        selection_state="selected",
        reason="fixture",
        score=1.0,
    )
    disclosure_id = await repository.record_synthetic_media_disclosure(
        distribution_package_id=distribution_package_id,
        decision={"required": True, "label": "Synthetic media"},
        status="approved",
    )
    policy_versions, creative_approval_id = await asyncio.to_thread(
        _insert_ready_package_governance,
        os.environ["TEST_DATABASE_URL"],
        seeded["workspace_id"],
        asset.asset_id,
        distribution_package_id,
        publication_proofs,
        publication_policy_document,
        asset_license_terms,
    )
    if publication_proofs:
        assert policy_versions is not None
        await repository.record_asset_rights_link(
            asset_id=asset.asset_id,
            link_key="publication-license",
            relation="asset_license",
            reference_id=policy_versions[1],
        )
    ready_package_id = await repository.record_ready_package(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        distribution_package_id=distribution_package_id,
        platform_profile_id=profile_id,
        disclosure_id=disclosure_id,
        ready_package_key="release-gate-ready",
        version=1,
        approval_state="approved",
        verifier_results={"all_passed": True},
        lineage={"brief_id": seeded["brief_id"]},
        approval_request_id=creative_approval_id,
        policy_versions=[policy_versions[0]] if policy_versions is not None else [],
    )
    return {
        "ready_package_id": ready_package_id,
        "workspace_id": seeded["workspace_id"],
        "program_id": seeded["program_id"],
        "disclosure_id": disclosure_id,
        "distribution_package_id": distribution_package_id,
        "candidate_id": candidate_id,
        "asset_id": asset.asset_id,
        "creative_approval_id": creative_approval_id,
    }


async def _approved_publication_approval(
    ready: dict[str, str],
    publisher_account_id: str,
    *,
    platform: str = "fixture",
    destination: str = "fixture://account",
    locale: str = "en",
    territory: str = "global",
    visibility: str = "private",
    capability_profile_version: int = 1,
) -> str:
    def insert() -> str:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO approval_requests (
                    workspace_id, effect_type, status, request_context, requested_by,
                    decided_by, decided_at, decision_reason
                ) VALUES (
                    %s, 'publication', 'approved', %s::jsonb, 'fixture-operator',
                    'fixture-operator', CURRENT_TIMESTAMP, 'fixture governed publication approval'
                )
                RETURNING id::text
                """,
                (
                    ready["workspace_id"],
                    json.dumps(
                        {
                            "ready_package_id": ready["ready_package_id"],
                            "publisher_account_id": publisher_account_id,
                            "platform": platform,
                            "destination": destination,
                            "locale": locale,
                            "territory": territory,
                            "visibility": visibility,
                            "capability_profile_version": capability_profile_version,
                        },
                        sort_keys=True,
                    ),
                ),
            )
            return str(cursor.fetchone()[0])

    return await asyncio.to_thread(insert)


def _insert_ready_package_governance(
    database_url: str,
    workspace_id: str,
    asset_id: str,
    distribution_package_id: str,
    publication_proofs: bool,
    publication_policy_document: dict[str, object] | None,
    asset_license_terms: dict[str, object] | None,
) -> tuple[tuple[str, str] | None, str]:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        policy_id: str | None = None
        license_id: str | None = None
        if publication_proofs:
            cursor.execute(
                """
                INSERT INTO policy_versions (
                    workspace_id, policy_name, version, status, document, document_hash
                ) VALUES (%s, 'release-gate-publication', '1', 'active', %s::jsonb, %s)
                RETURNING id::text
                """,
                (workspace_id, json.dumps(publication_policy_document or {}), "p" * 64),
            )
            policy_id = str(cursor.fetchone()[0])
            cursor.execute(
                """
                INSERT INTO asset_licenses (
                    asset_id, license_type, commercial_use, terms, status
                ) VALUES (%s, 'fixture', true, %s::jsonb, 'active')
                RETURNING id::text
                """,
                (asset_id, json.dumps(asset_license_terms or {})),
            )
            license_id = str(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO approval_requests (
                workspace_id, effect_type, status, request_context, requested_by,
                decided_by, decided_at, decision_reason
            ) VALUES (
                %s, 'creative_distribution', 'approved', %s::jsonb, 'fixture-operator',
                'fixture-operator', CURRENT_TIMESTAMP, 'fixture ready package approval'
            )
            RETURNING id::text
            """,
            (
                workspace_id,
                json.dumps({"distribution_package_id": distribution_package_id}, sort_keys=True),
            ),
        )
        creative_approval_id = str(cursor.fetchone()[0])
        return (policy_id, license_id) if policy_id and license_id else None, creative_approval_id


@pytest.mark.asyncio
async def test_ready_package_referenced_disclosure_rejects_direct_update() -> None:
    approved_package = await _approved_ready_package()

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        with pytest.raises(
            psycopg.errors.RaiseException,
            match="immutable approved distribution decision",
        ):
            connection.execute(
                "UPDATE synthetic_media_disclosures SET status = 'rejected' WHERE id = %s",
                (approved_package["disclosure_id"],),
            )


@pytest.mark.asyncio
async def test_ready_package_referenced_distribution_rejects_direct_update() -> None:
    approved_package = await _approved_ready_package()

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        with pytest.raises(
            psycopg.errors.RaiseException,
            match="immutable approved distribution decision",
        ):
            connection.execute(
                "UPDATE distribution_packages SET status = 'rejected' WHERE id = %s",
                (approved_package["distribution_package_id"],),
            )


@pytest.mark.asyncio
async def test_ready_package_referenced_title_candidate_rejects_direct_update() -> None:
    approved_package = await _approved_ready_package()

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        with pytest.raises(
            psycopg.errors.RaiseException,
            match="immutable approved distribution decision",
        ):
            connection.execute(
                "UPDATE title_thumbnail_candidates SET title = 'changed' WHERE id = %s",
                (approved_package["candidate_id"],),
            )
