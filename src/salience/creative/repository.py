"""Canonical idempotent persistence and reverse lineage for creative production."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from salience.creative.contracts import ProviderWebhookEvent


@dataclass(frozen=True)
class CreativeAssetVariant:
    asset_id: str
    asset_variant_id: str


@dataclass(frozen=True)
class ProviderJobLifecycle:
    provider_job_id: str
    state: str
    terminal_at: datetime | None
    next_poll_after: datetime | None
    retry_after: datetime | None
    actual_cost_status: str
    cancel_requested_at: datetime | None


@dataclass(frozen=True)
class CreativeWebhookReceipt:
    receipt_id: str
    provider_job_id: str
    state: str


_TERMINAL_PROVIDER_STATES = frozenset({"completed", "failed", "cancelled", "dead_lettered"})
_PROVIDER_STATE_TRANSITIONS = {
    "planned": frozenset({"submitting", "submitted", "failed", "cancelled", "dead_lettered"}),
    "submitting": frozenset({"submitted", "running", "failed", "cancelled", "dead_lettered"}),
    "submitted": frozenset({"running", "completed", "failed", "cancelled", "dead_lettered"}),
    "running": frozenset({"completed", "failed", "cancelled", "dead_lettered"}),
}


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

    async def record_creative_brief(
        self,
        *,
        workspace_id: str,
        program_id: str,
        brief_id: str,
        script_id: str,
        creative_key: str,
        version: int,
        status: str,
        creative_plan: dict[str, Any],
        trace_id: str,
        span_id: str | None = None,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO creative_briefs (
                workspace_id, content_program_id, content_brief_id, script_version_id, creative_key,
                version, status, creative_plan, provenance, trace_id, span_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
            ON CONFLICT (content_program_id, creative_key, version)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                brief_id,
                script_id,
                creative_key,
                version,
                status,
                _json(creative_plan),
                _json({"script_id": script_id}),
                trace_id,
                span_id,
            ),
        )

    async def record_storyboard(
        self,
        *,
        creative_brief_id: str,
        version: int,
        status: str,
        content: dict[str, Any],
        trace_id: str,
        span_id: str | None = None,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO storyboards (
                creative_brief_id, version, status, content, provenance, trace_id, span_id
            ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
            ON CONFLICT (creative_brief_id, version)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                creative_brief_id,
                version,
                status,
                _json(content),
                _json({"creative_brief_id": creative_brief_id}),
                trace_id,
                span_id,
            ),
        )

    async def provider_job_for_creative(
        self, *, creative_job_id: str, provider_id: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._provider_job_for_creative, creative_job_id, provider_id
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

    async def transition_provider_job(
        self,
        *,
        provider_job_id: str,
        state: str,
        trace_id: str,
        span_id: str | None = None,
        actual_cost_micros: int | None = None,
        failure_class: str | None = None,
        next_poll_after_seconds: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> ProviderJobLifecycle:
        if state not in {*_PROVIDER_STATE_TRANSITIONS, *_TERMINAL_PROVIDER_STATES}:
            raise ValueError(f"unsupported provider lifecycle state: {state}")
        if actual_cost_micros is not None and actual_cost_micros < 0:
            raise ValueError("actual_cost_micros must be non-negative")
        if next_poll_after_seconds is not None and next_poll_after_seconds < 0:
            raise ValueError("next_poll_after_seconds must be non-negative")
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative")
        return await asyncio.to_thread(
            self._transition_provider_job,
            provider_job_id,
            state,
            trace_id,
            span_id,
            actual_cost_micros,
            failure_class,
            next_poll_after_seconds,
            retry_after_seconds,
        )

    async def request_provider_cancellation(
        self, *, provider_job_id: str, trace_id: str, span_id: str | None = None
    ) -> ProviderJobLifecycle:
        return await asyncio.to_thread(
            self._request_provider_cancellation, provider_job_id, trace_id, span_id
        )

    async def record_verified_webhook(
        self, *, event: ProviderWebhookEvent, trace_id: str, span_id: str | None = None
    ) -> CreativeWebhookReceipt:
        return await asyncio.to_thread(self._record_verified_webhook, event, trace_id, span_id)

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

    async def record_title_thumbnail_candidate(
        self,
        *,
        distribution_package_id: str,
        candidate_key: str,
        title: str,
        thumbnail_asset_id: str | None,
        selection_state: str,
        reason: str | None = None,
        score: float | None = None,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO title_thumbnail_candidates (
                distribution_package_id, candidate_key, title, thumbnail_asset_id, score,
                selection_state, reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (distribution_package_id, candidate_key)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                distribution_package_id,
                candidate_key,
                title,
                thumbnail_asset_id,
                score,
                selection_state,
                reason,
            ),
        )

    async def record_localization(
        self,
        *,
        distribution_package_id: str,
        source_locale: str,
        target_locale: str,
        content: dict[str, Any],
        claim_ids: list[str],
        status: str,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO localizations (
                distribution_package_id, source_locale, target_locale, content, claim_ids, status
            ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (distribution_package_id, target_locale)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                distribution_package_id,
                source_locale,
                target_locale,
                _json(content),
                _json(claim_ids),
                status,
            ),
        )

    async def record_originality_evaluation(
        self,
        *,
        distribution_package_id: str,
        evaluator_version: str,
        metrics: dict[str, Any],
        status: str,
        reason: str,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO originality_evaluations (
                distribution_package_id, evaluator_version, metrics, status, reason
            ) VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (distribution_package_id, evaluator_version)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (distribution_package_id, evaluator_version, _json(metrics), status, reason),
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
                    CASE WHEN %s IN ('completed', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP ELSE NULL END,
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

    def _provider_job_for_creative(
        self, creative_job_id: str, provider_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, external_job_id, state, reconciliation_state
                FROM provider_jobs
                WHERE creative_job_id = %s AND provider_id = %s
                """,
                (creative_job_id, provider_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "provider_job_id": row[0],
                "external_job_id": row[1],
                "state": row[2],
                "reconciliation_state": row[3],
            }

    def _transition_provider_job(
        self,
        provider_job_id: str,
        state: str,
        trace_id: str,
        span_id: str | None,
        actual_cost_micros: int | None,
        failure_class: str | None,
        next_poll_after_seconds: int | None,
        retry_after_seconds: int | None,
    ) -> ProviderJobLifecycle:
        with self._connect() as connection, connection.cursor() as cursor:
            current = self._provider_lifecycle_for_update(cursor, provider_job_id)
            if current.state != state:
                allowed = _PROVIDER_STATE_TRANSITIONS.get(current.state, frozenset())
                if state not in allowed:
                    if current.state in _TERMINAL_PROVIDER_STATES:
                        raise ValueError("terminal provider job cannot transition")
                    raise ValueError(
                        f"provider lifecycle transition {current.state}->{state} is not allowed"
                    )
            terminal = state in _TERMINAL_PROVIDER_STATES
            cursor.execute(
                """
                UPDATE provider_jobs
                SET state = %s,
                    actual_cost_micros = COALESCE(%s, actual_cost_micros),
                    actual_cost_status = CASE WHEN %s::bigint IS NULL
                        THEN actual_cost_status ELSE 'known' END,
                    failure_class = COALESCE(%s, failure_class),
                    terminal_at = CASE WHEN %s THEN COALESCE(terminal_at, CURRENT_TIMESTAMP)
                        ELSE terminal_at END,
                    completed_at = CASE WHEN %s THEN COALESCE(completed_at, CURRENT_TIMESTAMP)
                        ELSE completed_at END,
                    next_poll_after = CASE
                        WHEN %s THEN NULL
                        WHEN %s::integer IS NULL THEN next_poll_after
                        ELSE CURRENT_TIMESTAMP + (%s * INTERVAL '1 second') END,
                    retry_after = CASE
                        WHEN %s::integer IS NULL THEN retry_after
                        ELSE CURRENT_TIMESTAMP + (%s * INTERVAL '1 second') END,
                    trace_id = %s,
                    span_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    state,
                    actual_cost_micros,
                    actual_cost_micros,
                    failure_class,
                    terminal,
                    terminal,
                    terminal,
                    next_poll_after_seconds,
                    next_poll_after_seconds,
                    retry_after_seconds,
                    retry_after_seconds,
                    trace_id,
                    span_id,
                    provider_job_id,
                ),
            )
            return self._provider_lifecycle_for_update(cursor, provider_job_id)

    def _request_provider_cancellation(
        self, provider_job_id: str, trace_id: str, span_id: str | None
    ) -> ProviderJobLifecycle:
        with self._connect() as connection, connection.cursor() as cursor:
            current = self._provider_lifecycle_for_update(cursor, provider_job_id)
            if current.state not in _TERMINAL_PROVIDER_STATES:
                cursor.execute(
                    """
                    UPDATE provider_jobs
                    SET cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                        trace_id = %s, span_id = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (trace_id, span_id, provider_job_id),
                )
            return self._provider_lifecycle_for_update(cursor, provider_job_id)

    def _record_verified_webhook(
        self, event: ProviderWebhookEvent, trace_id: str, span_id: str | None
    ) -> CreativeWebhookReceipt:
        delivery_identity = event.delivery_id or event.safe_payload_hash
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text FROM provider_jobs
                WHERE provider_id = %s AND external_job_id = %s FOR UPDATE
                """,
                (event.provider_id, event.external_job_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError("provider webhook does not match a canonical provider job")
            provider_job_id = row[0]
            cursor.execute(
                """
                INSERT INTO creative_provider_webhook_receipts (
                    provider_job_id, provider_id, delivery_identity, safe_payload_hash,
                    signature_verified, state, trace_id, span_id
                ) VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s)
                ON CONFLICT (provider_id, delivery_identity)
                DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                RETURNING id::text, provider_job_id::text, state
                """,
                (provider_job_id, event.provider_id, delivery_identity, event.safe_payload_hash,
                 event.state, trace_id, span_id),
            )
            receipt_id, persisted_provider_job_id, state = cursor.fetchone()
        self._transition_provider_job(
            provider_job_id, event.state, trace_id, span_id,
            event.usage.actual_micros if event.usage is not None else None,
            None, None, None,
        )
        return CreativeWebhookReceipt(receipt_id, persisted_provider_job_id, state)

    @staticmethod
    def _provider_lifecycle_for_update(
        cursor: psycopg.Cursor[Any], provider_job_id: str
    ) -> ProviderJobLifecycle:
        cursor.execute(
            """
            SELECT id::text, state, terminal_at, next_poll_after, retry_after, actual_cost_status,
                   cancel_requested_at
            FROM provider_jobs WHERE id = %s FOR UPDATE
            """,
            (provider_job_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"provider job not found: {provider_job_id}")
        return ProviderJobLifecycle(*row)

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
