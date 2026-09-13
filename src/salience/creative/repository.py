"""Canonical idempotent persistence and reverse lineage for creative production."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass(frozen=True)
class CreativeAssetVariant:
    asset_id: str
    asset_variant_id: str


class CreativeRepository:
    """Persist provider-neutral Phase 7-8 records without storing media bytes."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def record_script(
        self,
        *,
        workspace_id: str,
        program_id: str,
        brief_id: str,
        script_key: str,
        version: int,
        status: str,
        target_format: str,
        target_duration_seconds: int,
        script: dict[str, Any],
        claim_ids: list[str],
        evidence_ids: list[str],
        trace_id: str,
        span_id: str | None = None,
        parent_script_id: str | None = None,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO script_versions (
                workspace_id, content_program_id, content_brief_id, parent_script_id, script_key,
                version, status, target_format, target_duration_seconds, script, claim_ids,
                evidence_ids, provenance, trace_id, span_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                %s::jsonb, %s::jsonb, %s, %s
            )
            ON CONFLICT (content_program_id, script_key, version)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                brief_id,
                parent_script_id,
                script_key,
                version,
                status,
                target_format,
                target_duration_seconds,
                _json(script),
                _json(claim_ids),
                _json(evidence_ids),
                _json({"brief_id": brief_id}),
                trace_id,
                span_id,
            ),
        )

    async def record_creative_job(
        self,
        *,
        workspace_id: str,
        program_id: str,
        brief_id: str,
        requested_capability: str,
        request_fingerprint: str,
        idempotency_key: str,
        request: dict[str, Any],
        timeout_seconds: int,
        trace_id: str,
        script_id: str | None = None,
        creative_brief_id: str | None = None,
        job_id: str | None = None,
        budget_reservation_id: str | None = None,
        state: str = "requested",
        span_id: str | None = None,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO creative_jobs (
                workspace_id, content_program_id, job_id, content_brief_id, script_version_id,
                creative_brief_id, requested_capability, request_fingerprint, idempotency_key,
                state, request, budget_reservation_id, timeout_seconds, trace_id, span_id,
                provenance
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                %s::jsonb
            )
            ON CONFLICT (content_program_id, request_fingerprint)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                job_id,
                brief_id,
                script_id,
                creative_brief_id,
                requested_capability,
                request_fingerprint,
                idempotency_key,
                state,
                _json(request),
                budget_reservation_id,
                timeout_seconds,
                trace_id,
                span_id,
                _json({"brief_id": brief_id, "script_id": script_id}),
            ),
        )

    async def record_provider_job(
        self,
        *,
        creative_job_id: str,
        provider_id: str,
        state: str,
        normalized_request: dict[str, Any],
        trace_id: str,
        provider_version: str | None = None,
        model_id: str | None = None,
        external_job_id: str | None = None,
        plugin_version_id: str | None = None,
        reconciliation_state: dict[str, Any] | None = None,
        provider_extension: dict[str, Any] | None = None,
        estimated_cost_micros: int = 0,
        actual_cost_micros: int | None = None,
        failure_class: str | None = None,
        span_id: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._record_provider_job,
            creative_job_id,
            provider_id,
            state,
            normalized_request,
            trace_id,
            provider_version,
            model_id,
            external_job_id,
            plugin_version_id,
            reconciliation_state or {},
            provider_extension or {},
            estimated_cost_micros,
            actual_cost_micros,
            failure_class,
            span_id,
        )

    async def record_asset_variant(
        self,
        *,
        workspace_id: str,
        program_id: str,
        creative_job_id: str,
        storage_key: str,
        content_hash: str,
        media_type: str,
        byte_size: int,
        origin_type: str,
        variant_key: str,
        selection_state: str,
        trace_id: str,
        provider_job_id: str | None = None,
        artifact_id: str | None = None,
        selection_reason: str | None = None,
        technical_properties: dict[str, Any] | None = None,
        creation_parameters: dict[str, Any] | None = None,
        span_id: str | None = None,
    ) -> CreativeAssetVariant:
        return await asyncio.to_thread(
            self._record_asset_variant,
            workspace_id,
            program_id,
            creative_job_id,
            storage_key,
            content_hash,
            media_type,
            byte_size,
            origin_type,
            variant_key,
            selection_state,
            trace_id,
            provider_job_id,
            artifact_id,
            selection_reason,
            technical_properties or {},
            creation_parameters or {},
            span_id,
        )

    async def select_asset(
        self, *, asset_variant_id: str, reason: str
    ) -> str:
        return await self._returning_id(
            """
            UPDATE asset_variants
            SET selection_state = 'selected', selection_reason = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING asset_id::text
            """,
            (reason, asset_variant_id),
        )

    async def record_platform_profile(
        self,
        *,
        workspace_id: str,
        program_id: str,
        profile_key: str,
        version: int,
        target_platform: str,
        rules: dict[str, Any],
        status: str,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO platform_profiles (
                workspace_id, content_program_id, profile_key, version, target_platform, rules, status
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (content_program_id, profile_key, version)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (workspace_id, program_id, profile_key, version, target_platform, _json(rules), status),
        )

    async def record_distribution_package(
        self,
        *,
        workspace_id: str,
        program_id: str,
        brief_id: str,
        script_id: str,
        platform_profile_id: str,
        package_key: str,
        version: int,
        locale: str,
        package_metadata: dict[str, Any],
        status: str,
        asset_ids: list[str],
        verifier_results: dict[str, Any] | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._record_distribution_package,
            workspace_id,
            program_id,
            brief_id,
            script_id,
            platform_profile_id,
            package_key,
            version,
            locale,
            package_metadata,
            status,
            asset_ids,
            verifier_results or {},
        )

    async def record_synthetic_media_disclosure(
        self,
        *,
        distribution_package_id: str,
        decision: dict[str, Any],
        status: str,
        policy_version_id: str | None = None,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO synthetic_media_disclosures (
                distribution_package_id, decision, status, policy_version_id
            ) VALUES (%s, %s::jsonb, %s, %s)
            ON CONFLICT (distribution_package_id)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (distribution_package_id, _json(decision), status, policy_version_id),
        )

    async def record_ready_package(
        self,
        *,
        workspace_id: str,
        program_id: str,
        brief_id: str,
        script_id: str,
        distribution_package_id: str,
        platform_profile_id: str,
        disclosure_id: str,
        ready_package_key: str,
        version: int,
        approval_state: str,
        verifier_results: dict[str, Any],
        lineage: dict[str, Any],
        approval_request_id: str | None = None,
        policy_versions: list[str] | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._record_ready_package,
            workspace_id,
            program_id,
            brief_id,
            script_id,
            distribution_package_id,
            platform_profile_id,
            disclosure_id,
            ready_package_key,
            version,
            approval_state,
            verifier_results,
            lineage,
            approval_request_id,
            policy_versions or [],
        )

    async def lineage_for_ready_package(self, ready_package_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._lineage_for_ready_package, ready_package_id)

    async def _returning_id(self, statement: str, values: tuple[Any, ...]) -> str:
        return await asyncio.to_thread(self._execute_returning_id, statement, values)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )

    def _execute_returning_id(self, statement: str, values: tuple[Any, ...]) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(statement, values)
            row = cursor.fetchone()
            if row is None:
                raise KeyError("canonical record was not found")
            return row[0]

    def _record_provider_job(
        self,
        creative_job_id: str,
        provider_id: str,
        state: str,
        normalized_request: dict[str, Any],
        trace_id: str,
        provider_version: str | None,
        model_id: str | None,
        external_job_id: str | None,
        plugin_version_id: str | None,
        reconciliation_state: dict[str, Any],
        provider_extension: dict[str, Any],
        estimated_cost_micros: int,
        actual_cost_micros: int | None,
        failure_class: str | None,
        span_id: str | None,
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO provider_jobs (
                    creative_job_id, plugin_version_id, provider_id, provider_version, model_id,
                    external_job_id, state, reconciliation_state, normalized_request,
                    provider_extension, estimated_cost_micros, actual_cost_micros, failure_class,
                    submitted_at, completed_at, trace_id, span_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s,
                    CASE WHEN %s::text IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
                    CASE WHEN %s IN ('succeeded', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP ELSE NULL END,
                    %s, %s
                )
                ON CONFLICT (creative_job_id, provider_id)
                DO UPDATE SET
                    external_job_id = COALESCE(provider_jobs.external_job_id, EXCLUDED.external_job_id),
                    state = EXCLUDED.state,
                    reconciliation_state = EXCLUDED.reconciliation_state,
                    actual_cost_micros = COALESCE(EXCLUDED.actual_cost_micros, provider_jobs.actual_cost_micros),
                    failure_class = EXCLUDED.failure_class,
                    completed_at = COALESCE(EXCLUDED.completed_at, provider_jobs.completed_at),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id::text, external_job_id
                """,
                (
                    creative_job_id,
                    plugin_version_id,
                    provider_id,
                    provider_version,
                    model_id,
                    external_job_id,
                    state,
                    _json(reconciliation_state),
                    _json(normalized_request),
                    _json(provider_extension),
                    estimated_cost_micros,
                    actual_cost_micros,
                    failure_class,
                    external_job_id,
                    state,
                    trace_id,
                    span_id,
                ),
            )
            provider_job_id, persisted_external_job_id = cursor.fetchone()
            if (
                external_job_id is not None
                and persisted_external_job_id is not None
                and persisted_external_job_id != external_job_id
            ):
                raise ValueError("provider job already reconciled to a different external ID")
            return provider_job_id

    def _record_asset_variant(
        self,
        workspace_id: str,
        program_id: str,
        creative_job_id: str,
        storage_key: str,
        content_hash: str,
        media_type: str,
        byte_size: int,
        origin_type: str,
        variant_key: str,
        selection_state: str,
        trace_id: str,
        provider_job_id: str | None,
        artifact_id: str | None,
        selection_reason: str | None,
        technical_properties: dict[str, Any],
        creation_parameters: dict[str, Any],
        span_id: str | None,
    ) -> CreativeAssetVariant:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO assets (
                    workspace_id, content_program_id, artifact_id, creative_job_id, storage_key,
                    content_hash, media_type, byte_size, origin_type, technical_properties,
                    creation_parameters, trace_id, span_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s
                )
                ON CONFLICT (content_program_id, content_hash, media_type)
                DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                RETURNING id::text
                """,
                (
                    workspace_id,
                    program_id,
                    artifact_id,
                    creative_job_id,
                    storage_key,
                    content_hash,
                    media_type,
                    byte_size,
                    origin_type,
                    _json(technical_properties),
                    _json(creation_parameters),
                    trace_id,
                    span_id,
                ),
            )
            asset_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO asset_variants (
                    asset_id, provider_job_id, variant_key, selection_state, selection_reason,
                    verifier_results
                ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)
                ON CONFLICT (asset_id, variant_key)
                DO UPDATE SET
                    selection_state = EXCLUDED.selection_state,
                    selection_reason = EXCLUDED.selection_reason,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id::text
                """,
                (asset_id, provider_job_id, variant_key, selection_state, selection_reason),
            )
            return CreativeAssetVariant(asset_id, cursor.fetchone()[0])

    def _record_distribution_package(
        self,
        workspace_id: str,
        program_id: str,
        brief_id: str,
        script_id: str,
        platform_profile_id: str,
        package_key: str,
        version: int,
        locale: str,
        package_metadata: dict[str, Any],
        status: str,
        asset_ids: list[str],
        verifier_results: dict[str, Any],
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO distribution_packages (
                    workspace_id, content_program_id, content_brief_id, script_version_id,
                    platform_profile_id, package_key, version, locale, metadata, status,
                    verifier_results
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                ON CONFLICT (content_program_id, package_key, version)
                DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                RETURNING id::text
                """,
                (
                    workspace_id,
                    program_id,
                    brief_id,
                    script_id,
                    platform_profile_id,
                    package_key,
                    version,
                    locale,
                    _json(package_metadata),
                    status,
                    _json(verifier_results),
                ),
            )
            distribution_package_id = cursor.fetchone()[0]
            for index, asset_id in enumerate(asset_ids):
                asset_role = "primary" if index == 0 else f"supplemental_{index}"
                cursor.execute(
                    """
                    INSERT INTO distribution_package_assets (
                        distribution_package_id, asset_id, asset_role
                    )
                    SELECT %s, id, %s FROM assets
                    WHERE id = %s AND content_program_id = %s
                    ON CONFLICT (distribution_package_id, asset_role)
                    DO UPDATE SET selection_reason = distribution_package_assets.selection_reason
                    RETURNING id::text
                    """,
                    (distribution_package_id, asset_role, asset_id, program_id),
                )
                if cursor.fetchone() is None:
                    raise ValueError("distribution package asset is outside the content program")
            return distribution_package_id

    def _record_ready_package(
        self,
        workspace_id: str,
        program_id: str,
        brief_id: str,
        script_id: str,
        distribution_package_id: str,
        platform_profile_id: str,
        disclosure_id: str,
        ready_package_key: str,
        version: int,
        approval_state: str,
        verifier_results: dict[str, Any],
        lineage: dict[str, Any],
        approval_request_id: str | None,
        policy_versions: list[str],
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM script_versions script
                JOIN distribution_packages package ON package.id = %s
                JOIN synthetic_media_disclosures disclosure ON disclosure.id = %s
                WHERE script.id = %s
                  AND script.content_program_id = %s
                  AND script.content_brief_id = %s
                  AND script.status = 'approved'
                  AND package.content_program_id = %s
                  AND package.content_brief_id = %s
                  AND package.script_version_id = script.id
                  AND package.platform_profile_id = %s
                  AND disclosure.distribution_package_id = package.id
                  AND disclosure.status = 'approved'
                """,
                (
                    distribution_package_id,
                    disclosure_id,
                    script_id,
                    program_id,
                    brief_id,
                    program_id,
                    brief_id,
                    platform_profile_id,
                ),
            )
            if cursor.fetchone() is None:
                raise ValueError("ready package requires an approved, consistent creative lineage")
            cursor.execute(
                """
                INSERT INTO ready_to_publish_packages (
                    workspace_id, content_program_id, content_brief_id, script_version_id,
                    distribution_package_id, platform_profile_id, disclosure_id, ready_package_key,
                    version, approval_request_id, approval_state, verifier_results, policy_versions,
                    lineage
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s::jsonb
                )
                ON CONFLICT (content_program_id, ready_package_key, version)
                DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                RETURNING id::text
                """,
                (
                    workspace_id,
                    program_id,
                    brief_id,
                    script_id,
                    distribution_package_id,
                    platform_profile_id,
                    disclosure_id,
                    ready_package_key,
                    version,
                    approval_request_id,
                    approval_state,
                    _json(verifier_results),
                    _json(policy_versions),
                    _json(lineage),
                ),
            )
            return cursor.fetchone()[0]

    def _lineage_for_ready_package(self, ready_package_id: str) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ready.content_brief_id::text, ready.script_version_id::text,
                       ready.distribution_package_id::text, ready.platform_profile_id::text,
                       brief.topic_opportunity_id::text, brief.strategic_package_id::text,
                       brief.claim_ids, opportunity.supporting_signal_ids
                FROM ready_to_publish_packages ready
                JOIN content_brief_versions brief ON brief.id = ready.content_brief_id
                JOIN topic_opportunities opportunity ON opportunity.id = brief.topic_opportunity_id
                WHERE ready.id = %s
                """,
                (ready_package_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(ready_package_id)
            (
                brief_id,
                script_id,
                distribution_package_id,
                platform_profile_id,
                opportunity_id,
                strategic_package_id,
                claim_ids,
                signal_ids,
            ) = row
            cursor.execute(
                """
                SELECT DISTINCT research_source_id::text, research_fetch_id::text,
                                research_evidence_id::text
                FROM signal_support
                WHERE signal_id = ANY(%s::uuid[])
                ORDER BY research_source_id::text, research_fetch_id::text, research_evidence_id::text
                """,
                (signal_ids,),
            )
            sources: list[str] = []
            fetches: list[str] = []
            evidence: list[str] = []
            for source_id, fetch_id, evidence_id in cursor.fetchall():
                if source_id not in sources:
                    sources.append(source_id)
                if fetch_id not in fetches:
                    fetches.append(fetch_id)
                if evidence_id not in evidence:
                    evidence.append(evidence_id)
            cursor.execute(
                """
                SELECT DISTINCT asset.id::text, provider_job.id::text, creative_job.id::text
                FROM distribution_package_assets package_asset
                JOIN assets asset ON asset.id = package_asset.asset_id
                LEFT JOIN asset_variants variant ON variant.asset_id = asset.id
                LEFT JOIN provider_jobs provider_job ON provider_job.id = variant.provider_job_id
                LEFT JOIN creative_jobs creative_job ON creative_job.id = asset.creative_job_id
                WHERE package_asset.distribution_package_id = %s
                ORDER BY asset.id::text, provider_job.id::text, creative_job.id::text
                """,
                (distribution_package_id,),
            )
            assets: list[str] = []
            provider_jobs: list[str] = []
            creative_jobs: list[str] = []
            for asset_id, provider_job_id, creative_job_id in cursor.fetchall():
                if asset_id not in assets:
                    assets.append(asset_id)
                if provider_job_id is not None and provider_job_id not in provider_jobs:
                    provider_jobs.append(provider_job_id)
                if creative_job_id is not None and creative_job_id not in creative_jobs:
                    creative_jobs.append(creative_job_id)
            cursor.execute(
                """
                SELECT DISTINCT agent_run.id::text
                FROM agent_runs agent_run
                JOIN creative_jobs creative_job ON creative_job.job_id = agent_run.job_id
                WHERE creative_job.id = ANY(%s::uuid[])
                ORDER BY agent_run.id::text
                """,
                (creative_jobs,),
            )
            agent_run_ids = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT DISTINCT model_invocation.id::text
                FROM model_invocations model_invocation
                WHERE model_invocation.parent_agent_run_id = ANY(%s::uuid[])
                ORDER BY model_invocation.id::text
                """,
                (agent_run_ids,),
            )
            model_invocation_ids = [row[0] for row in cursor.fetchall()]
        return {
            "ready_package_id": ready_package_id,
            "brief_id": brief_id,
            "script_id": script_id,
            "distribution_package_id": distribution_package_id,
            "platform_profile_id": platform_profile_id,
            "opportunity_id": opportunity_id,
            "strategic_package_id": strategic_package_id,
            "claim_ids": claim_ids,
            "signal_ids": signal_ids,
            "source_ids": sources,
            "fetch_ids": fetches,
            "evidence_ids": evidence,
            "asset_ids": assets,
            "provider_job_ids": provider_jobs,
            "creative_job_ids": creative_jobs,
            "agent_run_ids": agent_run_ids,
            "model_invocation_ids": model_invocation_ids,
            "tool_ids": [],
        }


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)
