"""Canonical idempotent persistence and reverse lineage for creative production."""

from __future__ import annotations

import asyncio
import hashlib
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


@dataclass(frozen=True)
class DistributionPackageRevision:
    distribution_package_id: str
    version: int


class ImmutableDecisionConflict(ValueError):
    """A governed decision differs from its existing immutable version."""


class DistributionRevisionRequired(ImmutableDecisionConflict):
    def __init__(self, distribution_package_id: str) -> None:
        super().__init__("approved distribution package requires an explicit revision")
        self.distribution_package_id = distribution_package_id


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

    async def record_asset_provenance(
        self,
        *,
        asset_id: str,
        origin_type: str,
        validation_status: str,
        c2pa_manifest_reference: str | None,
        signer_metadata: dict[str, Any],
        ingredients: list[dict[str, Any]] | None = None,
        transformations: list[dict[str, Any]] | None = None,
    ) -> str:
        return await self._returning_id(
            """
            INSERT INTO asset_provenance (
                asset_id, origin_type, ingredients, transformations, c2pa_manifest_reference,
                validation_status, signer_metadata
            ) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb)
            ON CONFLICT (asset_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                asset_id,
                origin_type,
                _json(ingredients or []),
                _json(transformations or []),
                c2pa_manifest_reference,
                validation_status,
                _json(signer_metadata),
            ),
        )

    async def asset_provenance(self, asset_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._asset_provenance, asset_id)

    async def asset_consent(self, asset_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._asset_consent, asset_id)

    async def asset_rights_context(self, asset_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._asset_rights_context, asset_id)

    async def record_asset_rights_link(
        self, *, asset_id: str, link_key: str, relation: str, reference_id: str
    ) -> str:
        columns = {
            "asset_license": "asset_license_id",
            "consent": "consent_record_id",
            "likeness": "likeness_identity_id",
            "voice": "voice_identity_id",
            "usage_restriction": "usage_restriction_id",
            "reference_asset": "reference_asset_id",
        }
        try:
            column = columns[relation]
        except KeyError as error:
            raise ValueError(f"unsupported asset rights relation: {relation}") from error
        return await self._returning_id(
            f"""
            INSERT INTO asset_rights_links (asset_id, link_key, {column})
            VALUES (%s, %s, %s)
            ON CONFLICT (asset_id, link_key) DO UPDATE SET {column} = EXCLUDED.{column},
                updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (asset_id, link_key, reference_id),
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
        return await asyncio.to_thread(
            self._record_platform_profile,
            workspace_id,
            program_id,
            profile_key,
            version,
            target_platform,
            rules,
            status,
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

    async def create_distribution_revision(
        self,
        distribution_package_id: str,
        *,
        selected_title: str,
        package_metadata: dict[str, Any] | None = None,
        verifier_results: dict[str, Any] | None = None,
        decision_fingerprint: str | None = None,
    ) -> DistributionPackageRevision:
        if not selected_title.strip():
            raise ValueError("selected_title is required")
        return await asyncio.to_thread(
            self._create_distribution_revision,
            distribution_package_id,
            selected_title,
            package_metadata,
            verifier_results,
            decision_fingerprint,
        )

    async def record_synthetic_media_disclosure(
        self,
        *,
        distribution_package_id: str,
        decision: dict[str, Any],
        status: str,
        policy_version_id: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._record_synthetic_media_disclosure,
            distribution_package_id,
            decision,
            status,
            policy_version_id,
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
        return await asyncio.to_thread(
            self._record_title_thumbnail_candidate,
            distribution_package_id,
            candidate_key,
            title,
            thumbnail_asset_id,
            score,
            selection_state,
            reason,
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
        return await asyncio.to_thread(
            self._record_localization,
            distribution_package_id,
            source_locale,
            target_locale,
            content,
            claim_ids,
            status,
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
        return await asyncio.to_thread(
            self._record_originality_evaluation,
            distribution_package_id,
            evaluator_version,
            metrics,
            status,
            reason,
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
                ON CONFLICT (provider_id, delivery_identity) DO NOTHING
                RETURNING id::text, provider_job_id::text, state
                """,
                (provider_job_id, event.provider_id, delivery_identity, event.safe_payload_hash,
                 event.state, trace_id, span_id),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                receipt_id, persisted_provider_job_id, state = inserted
            else:
                cursor.execute(
                    """
                    SELECT id::text, provider_job_id::text, safe_payload_hash, state
                    FROM creative_provider_webhook_receipts
                    WHERE provider_id = %s AND delivery_identity = %s
                    FOR UPDATE
                    """,
                    (event.provider_id, delivery_identity),
                )
                existing = cursor.fetchone()
                if existing is None:
                    raise RuntimeError("webhook receipt conflict could not be loaded")
                receipt_id, persisted_provider_job_id, payload_hash, state = existing
                if (persisted_provider_job_id, payload_hash, state) != (
                    provider_job_id,
                    event.safe_payload_hash,
                    event.state,
                ):
                    raise ValueError("webhook receipt differs from its immutable delivery identity")
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

    def _asset_provenance(self, asset_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT origin_type, validation_status, c2pa_manifest_reference, signer_metadata,
                       ingredients, transformations
                FROM asset_provenance WHERE asset_id = %s
                """,
                (asset_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "origin_type": row[0],
                "validation_status": row[1],
                "c2pa_manifest_reference": row[2],
                "signer_metadata": row[3],
                "ingredients": row[4],
                "transformations": row[5],
            }

    def _asset_consent(self, asset_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT consent.status, consent.permitted_channels, consent.commercial_use,
                       consent.territories, consent.expires_at, consent.revoked_at
                FROM asset_rights_links link
                JOIN consent_records consent ON consent.id = link.consent_record_id
                WHERE link.asset_id = %s
                ORDER BY link.created_at
                LIMIT 1
                """,
                (asset_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "status": row[0],
                "permitted_channels": row[1],
                "commercial_use": row[2],
                "territories": row[3],
                "expires_at": row[4],
                "revoked_at": row[5],
            }

    def _asset_rights_context(self, asset_id: str) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    link.consent_record_id::text,
                    direct_consent.status, direct_consent.permitted_channels,
                    direct_consent.commercial_use, direct_consent.territories,
                    direct_consent.expires_at, direct_consent.revoked_at,
                    link.likeness_identity_id::text, likeness.status,
                    likeness_consent.status, likeness_consent.permitted_channels,
                    likeness_consent.commercial_use, likeness_consent.territories,
                    likeness_consent.expires_at, likeness_consent.revoked_at,
                    link.voice_identity_id::text, voice.status,
                    voice_consent.status, voice_consent.permitted_channels,
                    voice_consent.commercial_use, voice_consent.territories,
                    voice_consent.expires_at, voice_consent.revoked_at,
                    license.id::text, license.status, license.commercial_use,
                    license.terms, license.expires_at,
                    restriction.id::text, restriction.status, restriction.document,
                    link.reference_asset_id::text
                FROM asset_rights_links link
                LEFT JOIN consent_records direct_consent ON direct_consent.id = link.consent_record_id
                LEFT JOIN likeness_identities likeness ON likeness.id = link.likeness_identity_id
                LEFT JOIN consent_records likeness_consent ON likeness_consent.id = likeness.consent_record_id
                LEFT JOIN voice_identities voice ON voice.id = link.voice_identity_id
                LEFT JOIN consent_records voice_consent ON voice_consent.id = voice.consent_record_id
                LEFT JOIN asset_licenses license ON license.id = link.asset_license_id
                LEFT JOIN usage_restrictions restriction ON restriction.id = link.usage_restriction_id
                WHERE link.asset_id = %s
                ORDER BY link.created_at, link.link_key
                """,
                (asset_id,),
            )
            context: dict[str, Any] = {
                "direct_consents": [],
                "likeness_consents": [],
                "voice_consents": [],
                "licenses": [],
                "restrictions": [],
                "reference_asset_ids": [],
            }
            for row in cursor.fetchall():
                (
                    direct_id,
                    direct_status,
                    direct_channels,
                    direct_commercial,
                    direct_territories,
                    direct_expires,
                    direct_revoked,
                    likeness_id,
                    likeness_status,
                    likeness_consent_status,
                    likeness_channels,
                    likeness_commercial,
                    likeness_territories,
                    likeness_expires,
                    likeness_revoked,
                    voice_id,
                    voice_status,
                    voice_consent_status,
                    voice_channels,
                    voice_commercial,
                    voice_territories,
                    voice_expires,
                    voice_revoked,
                    license_id,
                    license_status,
                    license_commercial,
                    license_terms,
                    license_expires,
                    restriction_id,
                    restriction_status,
                    restriction_document,
                    reference_asset_id,
                ) = row
                if direct_id is not None:
                    context["direct_consents"].append(
                        _consent_facts(
                            direct_status,
                            direct_channels,
                            direct_commercial,
                            direct_territories,
                            direct_expires,
                            direct_revoked,
                        )
                    )
                if likeness_id is not None:
                    context["likeness_consents"].append(
                        {
                            "identity_status": likeness_status,
                            "consent": (
                                _consent_facts(
                                    likeness_consent_status,
                                    likeness_channels,
                                    likeness_commercial,
                                    likeness_territories,
                                    likeness_expires,
                                    likeness_revoked,
                                )
                                if likeness_consent_status is not None
                                else None
                            ),
                        }
                    )
                if voice_id is not None:
                    context["voice_consents"].append(
                        {
                            "identity_status": voice_status,
                            "consent": (
                                _consent_facts(
                                    voice_consent_status,
                                    voice_channels,
                                    voice_commercial,
                                    voice_territories,
                                    voice_expires,
                                    voice_revoked,
                                )
                                if voice_consent_status is not None
                                else None
                            ),
                        }
                    )
                if license_id is not None:
                    context["licenses"].append(
                        {
                            "status": license_status,
                            "commercial_use": license_commercial,
                            "terms": license_terms,
                            "expires_at": license_expires,
                        }
                    )
                if restriction_id is not None:
                    context["restrictions"].append(
                        {"status": restriction_status, "document": restriction_document}
                    )
                if reference_asset_id is not None:
                    context["reference_asset_ids"].append(reference_asset_id)
            return context

    def _record_platform_profile(
        self,
        workspace_id: str,
        program_id: str,
        profile_key: str,
        version: int,
        target_platform: str,
        rules: dict[str, Any],
        status: str,
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform_profiles (
                    workspace_id, content_program_id, profile_key, version, target_platform, rules, status
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (content_program_id, profile_key, version) DO NOTHING
                RETURNING id::text
                """,
                (workspace_id, program_id, profile_key, version, target_platform, _json(rules), status),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return inserted[0]
            cursor.execute(
                """
                SELECT id::text, workspace_id::text, target_platform, rules, status
                FROM platform_profiles
                WHERE content_program_id = %s AND profile_key = %s AND version = %s
                """,
                (program_id, profile_key, version),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError("platform profile was not found after conflict")
            profile_id, existing_workspace, existing_platform, existing_rules, existing_status = existing
            if (
                existing_workspace == workspace_id
                and existing_platform == target_platform
                and existing_rules == rules
                and existing_status == status
            ):
                return profile_id
            raise ImmutableDecisionConflict("platform profile differs; create a new profile version")

    def _record_synthetic_media_disclosure(
        self,
        distribution_package_id: str,
        decision: dict[str, Any],
        status: str,
        policy_version_id: str | None,
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO synthetic_media_disclosures (
                    distribution_package_id, decision, status, policy_version_id
                ) VALUES (%s, %s::jsonb, %s, %s)
                ON CONFLICT (distribution_package_id) DO NOTHING
                RETURNING id::text
                """,
                (distribution_package_id, _json(decision), status, policy_version_id),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return inserted[0]
            cursor.execute(
                """
                SELECT id::text, decision, status, policy_version_id::text
                FROM synthetic_media_disclosures WHERE distribution_package_id = %s
                """,
                (distribution_package_id,),
            )
            existing = cursor.fetchone()
            if existing is not None and existing[1:] == (decision, status, policy_version_id):
                return existing[0]
            raise ImmutableDecisionConflict("synthetic media disclosure differs from its immutable version")

    def _record_title_thumbnail_candidate(
        self,
        distribution_package_id: str,
        candidate_key: str,
        title: str,
        thumbnail_asset_id: str | None,
        score: float | None,
        selection_state: str,
        reason: str | None,
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO title_thumbnail_candidates (
                    distribution_package_id, candidate_key, title, thumbnail_asset_id, score,
                    selection_state, reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (distribution_package_id, candidate_key) DO NOTHING
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
            inserted = cursor.fetchone()
            if inserted is not None:
                return inserted[0]
            cursor.execute(
                """
                SELECT id::text, title, thumbnail_asset_id::text, score, selection_state, reason
                FROM title_thumbnail_candidates
                WHERE distribution_package_id = %s AND candidate_key = %s
                """,
                (distribution_package_id, candidate_key),
            )
            existing = cursor.fetchone()
            if existing is not None:
                existing_score = float(existing[3]) if existing[3] is not None else None
                if existing[1:] == (title, thumbnail_asset_id, existing[3], selection_state, reason) and (
                    existing_score == score
                ):
                    return existing[0]
            raise ImmutableDecisionConflict("title candidate differs from its immutable version")

    def _record_localization(
        self,
        distribution_package_id: str,
        source_locale: str,
        target_locale: str,
        content: dict[str, Any],
        claim_ids: list[str],
        status: str,
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO localizations (
                    distribution_package_id, source_locale, target_locale, content, claim_ids, status
                ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
                ON CONFLICT (distribution_package_id, target_locale) DO NOTHING
                RETURNING id::text
                """,
                (distribution_package_id, source_locale, target_locale, _json(content), _json(claim_ids), status),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return inserted[0]
            cursor.execute(
                """
                SELECT id::text, source_locale, content, claim_ids, status
                FROM localizations
                WHERE distribution_package_id = %s AND target_locale = %s
                """,
                (distribution_package_id, target_locale),
            )
            existing = cursor.fetchone()
            if existing is not None and existing[1:] == (source_locale, content, claim_ids, status):
                return existing[0]
            raise ImmutableDecisionConflict("localization differs from its immutable version")

    def _record_originality_evaluation(
        self,
        distribution_package_id: str,
        evaluator_version: str,
        metrics: dict[str, Any],
        status: str,
        reason: str,
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO originality_evaluations (
                    distribution_package_id, evaluator_version, metrics, status, reason
                ) VALUES (%s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (distribution_package_id, evaluator_version) DO NOTHING
                RETURNING id::text
                """,
                (distribution_package_id, evaluator_version, _json(metrics), status, reason),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return inserted[0]
            cursor.execute(
                """
                SELECT id::text, metrics, status, reason
                FROM originality_evaluations
                WHERE distribution_package_id = %s AND evaluator_version = %s
                """,
                (distribution_package_id, evaluator_version),
            )
            existing = cursor.fetchone()
            if existing is not None and existing[1:] == (metrics, status, reason):
                return existing[0]
            raise ImmutableDecisionConflict("originality evaluation differs from its immutable version")

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
                ON CONFLICT (content_program_id, package_key, version) DO NOTHING
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
            inserted = cursor.fetchone()
            if inserted is not None:
                distribution_package_id = inserted[0]
            else:
                cursor.execute(
                    """
                    SELECT id::text, workspace_id::text, content_brief_id::text,
                           script_version_id::text, platform_profile_id::text, locale, metadata,
                           status, verifier_results
                    FROM distribution_packages
                    WHERE content_program_id = %s AND package_key = %s AND version = %s
                    """,
                    (program_id, package_key, version),
                )
                existing = cursor.fetchone()
                if existing is None:
                    raise KeyError("distribution package was not found after conflict")
                (
                    distribution_package_id,
                    existing_workspace,
                    existing_brief,
                    existing_script,
                    existing_profile,
                    existing_locale,
                    existing_metadata,
                    existing_status,
                    existing_verifier,
                ) = existing
                if (
                    existing_workspace != workspace_id
                    or existing_brief != brief_id
                    or existing_script != script_id
                    or existing_profile != platform_profile_id
                    or existing_locale != locale
                    or existing_metadata != package_metadata
                    or existing_status != status
                    or existing_verifier != verifier_results
                ):
                    cursor.execute(
                        """
                        SELECT 1 FROM ready_to_publish_packages
                        WHERE distribution_package_id = %s AND approval_state = 'approved'
                        """,
                        (distribution_package_id,),
                    )
                    if cursor.fetchone() is not None:
                        raise DistributionRevisionRequired(distribution_package_id)
                    raise ImmutableDecisionConflict(
                        "distribution package differs; create a new package version"
                    )
            for index, asset_id in enumerate(asset_ids):
                asset_role = "primary" if index == 0 else f"supplemental_{index}"
                cursor.execute(
                    """
                    INSERT INTO distribution_package_assets (
                        distribution_package_id, asset_id, asset_role
                    )
                    SELECT %s, id, %s FROM assets
                    WHERE id = %s AND content_program_id = %s
                    ON CONFLICT (distribution_package_id, asset_role) DO NOTHING
                    RETURNING id::text
                    """,
                    (distribution_package_id, asset_role, asset_id, program_id),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        """
                        SELECT 1 FROM distribution_package_assets
                        WHERE distribution_package_id = %s AND asset_role = %s AND asset_id = %s
                        """,
                        (distribution_package_id, asset_role, asset_id),
                    )
                    if cursor.fetchone() is None:
                        raise ImmutableDecisionConflict(
                            "distribution package asset differs from its immutable version"
                        )
            return distribution_package_id

    def _create_distribution_revision(
        self,
        distribution_package_id: str,
        selected_title: str,
        package_metadata: dict[str, Any] | None,
        verifier_results: dict[str, Any] | None,
        decision_fingerprint: str | None,
    ) -> DistributionPackageRevision:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT workspace_id::text, content_program_id::text, content_brief_id::text,
                       script_version_id::text, platform_profile_id::text, package_key, version,
                       locale, metadata, status, verifier_results
                FROM distribution_packages
                WHERE id = %s
                FOR UPDATE
                """,
                (distribution_package_id,),
            )
            source = cursor.fetchone()
            if source is None:
                raise KeyError(f"distribution package not found: {distribution_package_id}")
            (
                workspace_id,
                program_id,
                brief_id,
                script_id,
                profile_id,
                package_key,
                source_version,
                locale,
                metadata,
                status,
                source_verifier_results,
            ) = source
            cursor.execute(
                """
                SELECT 1
                FROM ready_to_publish_packages
                WHERE distribution_package_id = %s AND approval_state = 'approved'
                """,
                (distribution_package_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError("distribution revision requires an approved source package")
            effective_metadata = (
                package_metadata
                if package_metadata is not None
                else (metadata if isinstance(metadata, dict) else {})
            )
            effective_verifier_results = (
                verifier_results
                if verifier_results is not None
                else (
                    source_verifier_results
                    if isinstance(source_verifier_results, dict)
                    else {}
                )
            )
            revision_fingerprint = decision_fingerprint or _revision_fingerprint(
                selected_title, effective_metadata, effective_verifier_results
            )
            cursor.execute(
                """
                SELECT package.id::text, package.version
                FROM distribution_packages package
                WHERE package.content_program_id = %s
                  AND package.package_key = %s
                  AND package.metadata ->> 'revision_of_distribution_package_id' = %s
                  AND package.metadata ->> 'decision_fingerprint' = %s
                ORDER BY package.version DESC
                LIMIT 1
                """,
                (program_id, package_key, distribution_package_id, revision_fingerprint),
            )
            existing = cursor.fetchone()
            if existing is not None:
                return DistributionPackageRevision(existing[0], int(existing[1]))
            cursor.execute(
                """
                SELECT COALESCE(max(version), %s)
                FROM distribution_packages
                WHERE content_program_id = %s AND package_key = %s
                """,
                (source_version, program_id, package_key),
            )
            next_version = int(cursor.fetchone()[0]) + 1
            revised_metadata = {
                "revision_of_distribution_package_id": distribution_package_id,
                "decision_fingerprint": revision_fingerprint,
                "source_metadata": effective_metadata,
            }
            cursor.execute(
                """
                INSERT INTO distribution_packages (
                    workspace_id, content_program_id, content_brief_id, script_version_id,
                    platform_profile_id, package_key, version, locale, metadata, status,
                    verifier_results
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                RETURNING id::text
                """,
                (
                    workspace_id,
                    program_id,
                    brief_id,
                    script_id,
                    profile_id,
                    package_key,
                    next_version,
                    locale,
                    _json(revised_metadata),
                    status,
                    _json(effective_verifier_results),
                ),
            )
            revision_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO distribution_package_assets (
                    distribution_package_id, asset_role, asset_id, selection_reason
                )
                SELECT %s, asset_role, asset_id, selection_reason
                FROM distribution_package_assets
                WHERE distribution_package_id = %s
                """,
                (revision_id, distribution_package_id),
            )
            return DistributionPackageRevision(revision_id, next_version)

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
                ON CONFLICT (content_program_id, ready_package_key, version) DO NOTHING
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
            inserted = cursor.fetchone()
            if inserted is not None:
                return inserted[0]
            cursor.execute(
                """
                SELECT id::text, workspace_id::text, content_brief_id::text, script_version_id::text,
                       distribution_package_id::text, platform_profile_id::text, disclosure_id::text,
                       approval_request_id::text, approval_state, verifier_results, policy_versions, lineage
                FROM ready_to_publish_packages
                WHERE content_program_id = %s AND ready_package_key = %s AND version = %s
                """,
                (program_id, ready_package_key, version),
            )
            existing = cursor.fetchone()
            expected = (
                workspace_id,
                brief_id,
                script_id,
                distribution_package_id,
                platform_profile_id,
                disclosure_id,
                approval_request_id,
                approval_state,
                verifier_results,
                policy_versions,
                lineage,
            )
            if existing is not None and existing[1:] == expected:
                return existing[0]
            raise ImmutableDecisionConflict("ready package differs from its immutable version")

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


def _revision_fingerprint(
    selected_title: str,
    package_metadata: dict[str, Any],
    verifier_results: dict[str, Any],
) -> str:
    payload = {
        "selected_title": selected_title,
        "package_metadata": package_metadata,
        "verifier_results": verifier_results,
    }
    return hashlib.sha256(_json(payload).encode()).hexdigest()


def _consent_facts(
    status: object,
    permitted_channels: object,
    commercial_use: object,
    territories: object,
    expires_at: object,
    revoked_at: object,
) -> dict[str, Any]:
    return {
        "status": status,
        "permitted_channels": permitted_channels,
        "commercial_use": commercial_use,
        "territories": territories,
        "expires_at": expires_at,
        "revoked_at": revoked_at,
    }
