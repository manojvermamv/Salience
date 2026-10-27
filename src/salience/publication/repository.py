"""Canonical, provider-neutral governed publication persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from salience.publication.contracts import (
    PublicationRequest,
    PublisherCapabilityProfile,
    PublisherWebhookEvent,
)
from salience.governance.production_policy import (
    ProductionApprovalBinding,
    ProductionPolicyVersion,
    parse_approval_row,
    parse_policy_row,
)


CURRENT_PUBLICATION_AUTHORIZATION_SQL = """
                SELECT request.id::text, request.workspace_id::text,
                       request.content_program_id::text, request.ready_package_id::text,
                       request.publisher_account_id::text, request.platform,
                       request.destination, request.locale, request.territory,
                       request.visibility, request.capability_profile_version,
                       request.request_key, request.publication_approval_request_id::text,
                       request.approval_reference, request.publisher_id, ready.approval_state,
                       CASE
                           WHEN publication_approval.status = 'approved'
                            AND publication_approval.workspace_id = request.workspace_id
                            AND publication_approval.effect_type = 'publication'
                            AND publication_approval.request_context @> jsonb_build_object(
                                'ready_package_id', request.ready_package_id::text,
                                'publisher_account_id', request.publisher_account_id::text,
                                'platform', request.platform,
                                'destination', request.destination,
                                'locale', request.locale,
                                'territory', request.territory,
                                'visibility', request.visibility,
                                'capability_profile_version', request.capability_profile_version
                           ) THEN 'approved'
                           WHEN publication_approval.status = 'approved'
                            AND publication_approval.workspace_id = request.workspace_id
                            AND publication_approval.request_context->>'schema_version' =
                                'salience.p1-effect-approval.v1'
                            AND publication_approval.request_context->>'publication_request_id' =
                                request.id::text
                            AND publication_approval.request_context->>'program_id' =
                                request.content_program_id::text
                            AND publication_approval.request_context->>'account_id' =
                                request.publisher_account_id::text
                            AND publication_approval.request_context->>'provider_id' =
                                request.publisher_id
                            AND publication_approval.effect_type = 'write'
                            AND publication_approval.policy_version_id =
                                ANY(ARRAY(SELECT jsonb_array_elements_text(request.policy_references)::uuid))
                            AND production_policy.workspace_id = request.workspace_id
                            AND production_policy.status = 'active'
                            AND production_policy.document @> jsonb_build_object(
                                'publication_scope', jsonb_build_object(
                                    'platform', request.platform,
                                    'destination', request.destination,
                                    'locale', request.locale,
                                    'territory', request.territory,
                                    'visibility', request.visibility
                                )
                            ) THEN 'approved'
                           WHEN publication_approval.status = 'approved' THEN 'invalid_scope'
                           ELSE publication_approval.status
                       END,
                       disclosure.status, account.workspace_id::text, account.account_type,
                       account.status, connection.publisher_account_id::text,
                       CASE
                           WHEN connection.revoked_at IS NOT NULL THEN 'revoked'
                           WHEN connection.expires_at IS NOT NULL
                            AND connection.expires_at <= CURRENT_TIMESTAMP THEN 'expired'
                           ELSE connection.status
                       END,
                       connection.granted_scopes, profile.capability_facts,
                       jsonb_array_length(request.policy_references) > 0
                       AND NOT EXISTS (
                           SELECT 1
                           FROM jsonb_array_elements_text(request.policy_references) AS reference(id)
                           LEFT JOIN policy_versions policy ON policy.id::text = reference.id
                           WHERE policy.id IS NULL
                              OR policy.workspace_id IS DISTINCT FROM request.workspace_id
                              OR policy.status <> 'active'
                              OR NOT (policy.document @> jsonb_build_object(
                                  'publication_scope', jsonb_build_object(
                                      'platform', request.platform,
                                      'destination', request.destination,
                                      'locale', request.locale,
                                      'territory', request.territory,
                                      'visibility', request.visibility
                                  )
                              ))
                       ),
                       EXISTS (
                           SELECT 1
                           FROM distribution_package_assets package_asset
                           WHERE package_asset.distribution_package_id = ready.distribution_package_id
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM distribution_package_assets package_asset
                           WHERE package_asset.distribution_package_id = ready.distribution_package_id
                             AND NOT EXISTS (
                                 SELECT 1
                                 FROM asset_rights_links rights
                                 WHERE rights.asset_id = package_asset.asset_id
                             )
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM distribution_package_assets package_asset
                           JOIN asset_rights_links rights
                             ON rights.asset_id = package_asset.asset_id
                           LEFT JOIN consent_records direct_consent
                             ON direct_consent.id = rights.consent_record_id
                           LEFT JOIN likeness_identities likeness
                             ON likeness.id = rights.likeness_identity_id
                           LEFT JOIN consent_records likeness_consent
                             ON likeness_consent.id = likeness.consent_record_id
                           LEFT JOIN voice_identities voice
                             ON voice.id = rights.voice_identity_id
                           LEFT JOIN consent_records voice_consent
                             ON voice_consent.id = voice.consent_record_id
                           LEFT JOIN asset_licenses license
                             ON license.id = rights.asset_license_id
                           LEFT JOIN usage_restrictions restriction
                             ON restriction.id = rights.usage_restriction_id
                           WHERE package_asset.distribution_package_id = ready.distribution_package_id
                             AND (
                                 (direct_consent.id IS NOT NULL AND (
                                     direct_consent.status <> 'active'
                                     OR direct_consent.revoked_at IS NOT NULL
                                     OR direct_consent.commercial_use IS NOT TRUE
                                     OR NOT (direct_consent.permitted_channels @> jsonb_build_array(request.platform))
                                     OR NOT (direct_consent.territories @> jsonb_build_array(request.territory))
                                     OR (direct_consent.expires_at IS NOT NULL
                                         AND direct_consent.expires_at <= CURRENT_TIMESTAMP)
                                 ))
                                 OR (likeness.id IS NOT NULL AND (
                                     likeness.status <> 'active'
                                     OR likeness_consent.status <> 'active'
                                     OR likeness_consent.revoked_at IS NOT NULL
                                     OR likeness_consent.commercial_use IS NOT TRUE
                                     OR NOT (likeness_consent.permitted_channels @> jsonb_build_array(request.platform))
                                     OR NOT (likeness_consent.territories @> jsonb_build_array(request.territory))
                                     OR (likeness_consent.expires_at IS NOT NULL
                                         AND likeness_consent.expires_at <= CURRENT_TIMESTAMP)
                                 ))
                                 OR (voice.id IS NOT NULL AND (
                                     voice.status <> 'active'
                                     OR voice_consent.status <> 'active'
                                     OR voice_consent.revoked_at IS NOT NULL
                                     OR voice_consent.commercial_use IS NOT TRUE
                                     OR NOT (voice_consent.permitted_channels @> jsonb_build_array(request.platform))
                                     OR NOT (voice_consent.territories @> jsonb_build_array(request.territory))
                                     OR (voice_consent.expires_at IS NOT NULL
                                         AND voice_consent.expires_at <= CURRENT_TIMESTAMP)
                                 ))
                                 OR (license.id IS NOT NULL AND (
                                     license.status <> 'active'
                                     OR license.commercial_use IS NOT TRUE
                                     OR NOT (license.terms @> jsonb_build_object(
                                         'permitted_channels', jsonb_build_array(request.platform),
                                         'territories', jsonb_build_array(request.territory)
                                     ))
                                     OR (license.expires_at IS NOT NULL
                                         AND license.expires_at <= CURRENT_TIMESTAMP)
                                 ))
                                 OR (restriction.id IS NOT NULL AND restriction.status = 'active')
                                 OR (
                                     direct_consent.id IS NULL
                                     AND likeness.id IS NULL
                                     AND voice.id IS NULL
                                     AND license.id IS NULL
                                     AND restriction.id IS NULL
                                 )
                                 )
                       )
                       , publication_approval.request_context,
                       publication_approval.decided_by,
                       publication_approval.decided_at,
                       production_policy.id::text,
                       production_policy.workspace_id::text,
                       production_policy.status,
                       production_policy.document,
                       production_policy.document_hash,
                       production_policy.effective_at,
                       database_clock.database_time,
                       request.policy_references @> jsonb_build_array(
                           publication_approval.policy_version_id::text
                       ),
                       publication_approval.status,
                       publication_approval.job_id::text,
                       publication_budget.status,
                       publication_approval.effect_type,
                       publication_approval.workspace_id::text,
                       publication_target.plan_id::text,
                       publication_target.plan_state,
                       publication_target.external_effect_id::text,
                       publication_target.external_effect_state,
                       publication_target.attempt_id::text,
                       publication_target.attempt_state,
                       publication_target.receipt_id::text,
                       publication_target.remote_id,
                       publication_target.receipt_state,
                       publication_target.publication_id::text,
                       publication_job.id::text,
                       publication_job.state,
                       publication_target.budget_reservation_id::text,
                       publication_target.publication_state
                FROM publication_requests request
                JOIN ready_to_publish_packages ready ON ready.id = request.ready_package_id
                JOIN synthetic_media_disclosures disclosure ON disclosure.id = ready.disclosure_id
                JOIN publisher_accounts account ON account.id = request.publisher_account_id
                LEFT JOIN approval_requests publication_approval
                  ON publication_approval.id = request.publication_approval_request_id
                LEFT JOIN LATERAL (
                    SELECT publisher_account_id, status, granted_scopes, expires_at, revoked_at
                    FROM publisher_connections
                    WHERE publisher_account_id = account.id
                    ORDER BY version DESC
                    LIMIT 1
                ) connection ON TRUE
                LEFT JOIN LATERAL (
                    SELECT capability_facts
                    FROM publisher_capability_profiles
                    WHERE id = request.publisher_capability_profile_id
                      AND workspace_id = request.workspace_id
                      AND publisher_account_id = request.publisher_account_id
                      AND publisher_id = request.publisher_id
                      AND platform = request.platform
                      AND profile_version = request.capability_profile_version
                      AND audit_state = 'verified'
                      AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                    ORDER BY observed_at DESC
                    LIMIT 1
                ) profile ON TRUE
                LEFT JOIN policy_versions production_policy
                  ON production_policy.id = publication_approval.policy_version_id
                LEFT JOIN LATERAL (
                    SELECT reservation.status
                    FROM publication_plans plan
                    LEFT JOIN budget_reservations reservation
                      ON reservation.id = plan.budget_reservation_id
                    WHERE plan.publication_request_id = request.id
                    ORDER BY plan.version DESC
                    LIMIT 1
                ) publication_budget ON TRUE
                LEFT JOIN LATERAL (
                    SELECT plan.id AS plan_id, plan.state AS plan_state,
                           plan.external_effect_id, plan.budget_reservation_id,
                           effect.status AS external_effect_state,
                           attempt.id AS attempt_id, attempt.state AS attempt_state,
                           receipt.id AS receipt_id, receipt.remote_id,
                           receipt.state AS receipt_state, publication.id AS publication_id,
                           publication.state AS publication_state
                    FROM publication_plans plan
                    LEFT JOIN external_effects effect ON effect.id = plan.external_effect_id
                    LEFT JOIN LATERAL (
                        SELECT attempt.id, attempt.state
                        FROM publication_attempts attempt
                        WHERE attempt.publication_plan_id = plan.id
                        ORDER BY attempt.attempt_number DESC
                        LIMIT 1
                    ) attempt ON TRUE
                    LEFT JOIN remote_publication_receipts receipt
                      ON receipt.publication_attempt_id = attempt.id
                    LEFT JOIN publications publication
                      ON publication.remote_publication_receipt_id = receipt.id
                    WHERE plan.publication_request_id = request.id
                    ORDER BY plan.version DESC
                    LIMIT 1
                ) publication_target ON TRUE
                LEFT JOIN jobs publication_job
                  ON publication_job.id = publication_approval.job_id
                CROSS JOIN LATERAL (SELECT clock_timestamp() AS database_time) database_clock
                WHERE request.id = %s
                """

@dataclass(frozen=True)
class PersistedPublisherAccount:
    id: str
    workspace_id: str
    platform: str
    account_key: str


@dataclass(frozen=True)
class PersistedPublicationRequest:
    id: str
    ready_package_id: str


@dataclass(frozen=True)
class PersistedPublicationExecution:
    """Immutable request and plan selected for one governed effect."""

    request: PublicationRequest
    plan_id: str
    publisher_id: str
    publisher_version: str
    schedule_id: str | None = None
    budget_id: str | None = None


@dataclass(frozen=True)
class CurrentPublicationAuthorization:
    """Fresh canonical facts required immediately before an external effect."""

    request: PublicationRequest
    ready_package_approval_state: str
    publication_approval_state: str | None
    disclosure_status: str
    account_workspace_id: str
    account_type: str
    account_status: str
    connection_account_id: str | None
    connection_status: str | None
    connection_scopes: frozenset[str]
    profile: PublisherCapabilityProfile | None
    policy_allowed: bool
    rights_allowed: bool
    policy_id: str | None = None
    policy_document_hash: str | None = None
    policy_status: str | None = None
    policy_expires_at: datetime | None = None
    production_policy: ProductionPolicyVersion | None = None
    approval_binding: ProductionApprovalBinding | None = None
    database_time: datetime | None = None
    budget_status: str | None = None
    publication_plan_id: str | None = None
    publication_plan_state: str | None = None
    external_effect_id: str | None = None
    external_effect_state: str | None = None
    publication_attempt_id: str | None = None
    publication_attempt_state: str | None = None
    remote_receipt_id: str | None = None
    remote_id: str | None = None
    remote_receipt_state: str | None = None
    publication_id: str | None = None
    job_id: str | None = None
    job_state: str | None = None
    budget_reservation_id: str | None = None
    publication_state: str | None = None


def _expected_production_approval(
    *,
    request: PublicationRequest,
    policy: ProductionPolicyVersion,
    approval_id: UUID,
    approval_workspace_id: UUID,
    approval_effect_type: str,
    approval_job_id: UUID | None,
    request_context: Mapping[str, object],
) -> ProductionApprovalBinding:
    def context_uuid(name: str) -> UUID:
        return UUID(str(request_context[name]))

    def context_datetime(name: str) -> datetime:
        value = request_context[name]
        if not isinstance(value, str):
            raise ValueError(f"tagged approval {name} must be a string")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"tagged approval {name} must be timezone-aware")
        return parsed

    scopes = request_context["required_scopes"]
    if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
        raise ValueError("tagged approval scopes are malformed")
    if request.publisher_id is None:
        raise ValueError("tagged publication approval has no canonical publisher")
    return ProductionApprovalBinding(
        approval_request_id=approval_id,
        workspace_id=approval_workspace_id,
        program_id=UUID(request.content_program_id),
        goal_id=context_uuid("goal_id"),
        goal_revision=request_context["goal_revision"],
        intent_id=context_uuid("intent_id"),
        cycle_id=context_uuid("cycle_id"),
        context_id=context_uuid("context_id"),
        operation_id=context_uuid("operation_id"),
        subject_id=context_uuid("subject_id"),
        policy_version_id=policy.id,
        policy_document_hash=policy.document_hash,
        effect_type=approval_effect_type,
        purpose=request_context["purpose"],
        artifact_sha256=request_context["artifact_sha256"],
        account_id=UUID(request.publisher_account_id),
        provider_id=request.publisher_id,
        required_scopes=tuple(scopes),
        budget_id=context_uuid("budget_id"),
        budget_reservation_id=context_uuid("budget_reservation_id"),
        expires_at=context_datetime("expires_at"),
        publication_request_id=UUID(request.id),
        job_id=approval_job_id,
        workflow_id=request_context["workflow_id"],
        workflow_run_id=request_context["workflow_run_id"],
        publication_plan_id=context_uuid("publication_plan_id"),
        publication_attempt_id=context_uuid("publication_attempt_id"),
        external_effect_id=context_uuid("external_effect_id")
        if "external_effect_id" in request_context
        else None,
        remote_receipt_id=context_uuid("remote_receipt_id")
        if "remote_receipt_id" in request_context
        else None,
    )


@dataclass(frozen=True)
class PersistedPublicationPlan:
    id: str
    publication_request_id: str


@dataclass(frozen=True)
class PersistedPublicationAttempt:
    id: str
    publication_plan_id: str


@dataclass(frozen=True)
class PersistedRemotePublicationReceipt:
    id: str
    publication_attempt_id: str
    remote_id: str


@dataclass(frozen=True)
class PersistedPublicationStatusEvent:
    id: str
    publication_attempt_id: str
    state: str


@dataclass(frozen=True)
class PersistedPublisherWebhookReceipt:
    id: str
    publication_attempt_id: str
    state: str

    @property
    def receipt_id(self) -> str:
        return self.id


@dataclass(frozen=True)
class PersistedPublication:
    id: str
    publication_request_id: str
    remote_receipt_id: str


@dataclass(frozen=True)
class PersistedPublicationSchedule:
    id: str
    publication_request_id: str
    publication_plan_id: str
    job_schedule_id: str
    version: int


class ImmutablePublicationConflict(ValueError):
    """An idempotency identity was replayed with a different governed decision."""


class PublicationRepository:
    """Persist immutable publication facts without provider credentials or media bytes."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def create_account(
        self,
        *,
        workspace_id: str,
        platform: str,
        account_key: str,
        account_type: str,
        external_account_reference: str,
    ) -> PersistedPublisherAccount:
        return await asyncio.to_thread(
            self._create_account,
            workspace_id,
            platform,
            account_key,
            account_type,
            external_account_reference,
        )

    async def create_request(
        self,
        *,
        ready_package_id: str,
        workspace_id: str,
        content_program_id: str,
        publisher_account_id: str,
        publication_approval_request_id: str,
        idempotency_key: str,
        platform: str = "fixture",
        destination: str = "fixture://account",
        locale: str = "en",
        territory: str = "global",
        visibility: str = "private",
        capability_profile_version: int = 1,
    ) -> PersistedPublicationRequest:
        return await asyncio.to_thread(
            self._create_request,
            ready_package_id,
            workspace_id,
            content_program_id,
            publisher_account_id,
            publication_approval_request_id,
            idempotency_key,
            platform,
            destination,
            locale,
            territory,
            visibility,
            capability_profile_version,
        )

    async def load_request(self, publication_request_id: str) -> PublicationRequest:
        return await asyncio.to_thread(self._load_request, publication_request_id)

    async def load_scheduled_execution(self, publication_schedule_id: str) -> PersistedPublicationExecution:
        return await asyncio.to_thread(self._load_scheduled_execution, publication_schedule_id)

    async def load_current_authorization(
        self, publication_request_id: str
    ) -> CurrentPublicationAuthorization:
        return await asyncio.to_thread(self._load_current_authorization, publication_request_id)

    async def create_plan(
        self,
        *,
        publication_request_id: str,
        version: int,
        publisher_id: str,
        publisher_version: str,
        external_effect_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> PersistedPublicationPlan:
        return await asyncio.to_thread(
            self._create_plan,
            publication_request_id,
            version,
            publisher_id,
            publisher_version,
            external_effect_id,
            trace_id,
            span_id,
        )

    async def create_attempt(
        self,
        *,
        publication_plan_id: str,
        attempt_number: int,
        idempotency_key: str,
    ) -> PersistedPublicationAttempt:
        return await asyncio.to_thread(
            self._create_attempt,
            publication_plan_id,
            attempt_number,
            idempotency_key,
        )

    async def create_schedule(
        self,
        *,
        publication_request_id: str,
        publication_plan_id: str,
        job_schedule_id: str,
        budget_id: str,
        version: int,
        schedule_fingerprint: str,
    ) -> PersistedPublicationSchedule:
        return await asyncio.to_thread(
            self._create_schedule,
            publication_request_id,
            publication_plan_id,
            job_schedule_id,
            budget_id,
            version,
            schedule_fingerprint,
        )

    async def record_remote_receipt(
        self,
        *,
        publication_attempt_id: str,
        publisher_id: str,
        remote_id: str,
        state: str,
        safe_metadata_hash: str,
        remote_url: str | None,
    ) -> PersistedRemotePublicationReceipt:
        return await asyncio.to_thread(
            self._record_remote_receipt,
            publication_attempt_id,
            publisher_id,
            remote_id,
            state,
            safe_metadata_hash,
            remote_url,
        )

    async def attach_reservation(
        self, *, publication_plan_id: str, budget_reservation_id: str
    ) -> None:
        await asyncio.to_thread(
            self._attach_reservation, publication_plan_id, budget_reservation_id
        )

    async def attach_external_effect(
        self, *, publication_plan_id: str, external_effect_id: str
    ) -> None:
        await asyncio.to_thread(
            self._attach_external_effect, publication_plan_id, external_effect_id
        )

    async def record_status_event(
        self,
        *,
        publication_attempt_id: str,
        state: str,
        source: str,
        safe_payload_hash: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> PersistedPublicationStatusEvent:
        return await asyncio.to_thread(
            self._record_status_event,
            publication_attempt_id,
            state,
            source,
            safe_payload_hash,
            trace_id,
            span_id,
        )

    async def record_verified_webhook(
        self,
        *,
        publication_attempt_id: str,
        status_event_id: str,
        publisher_id: str,
        delivery_identity: str,
        safe_payload_hash: str,
        state: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> PersistedPublisherWebhookReceipt:
        return await asyncio.to_thread(
            self._record_verified_webhook,
            publication_attempt_id,
            status_event_id,
            publisher_id,
            delivery_identity,
            safe_payload_hash,
            state,
            trace_id,
            span_id,
        )

    async def record_verified_webhook_event(
        self, event: PublisherWebhookEvent, *, trace_id: str
    ) -> PersistedPublisherWebhookReceipt:
        attempt_id = await asyncio.to_thread(
            self._attempt_for_remote, event.publisher_id, event.remote_id
        )
        status_event = await self.record_status_event(
            publication_attempt_id=attempt_id,
            state=event.state,
            source="webhook",
            safe_payload_hash=event.safe_payload_hash,
            trace_id=trace_id,
        )
        return await self.record_verified_webhook(
            publication_attempt_id=attempt_id,
            status_event_id=status_event.id,
            publisher_id=event.publisher_id,
            delivery_identity=event.delivery_identity,
            safe_payload_hash=event.safe_payload_hash,
            state=event.state,
            trace_id=trace_id,
        )

    async def record_publication(
        self,
        *,
        publication_request_id: str,
        remote_receipt_id: str,
        state: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> PersistedPublication:
        return await asyncio.to_thread(
            self._record_publication,
            publication_request_id,
            remote_receipt_id,
            state,
            trace_id,
            span_id,
        )

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )

    def _create_account(
        self,
        workspace_id: str,
        platform: str,
        account_key: str,
        account_type: str,
        external_account_reference: str,
    ) -> PersistedPublisherAccount:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publisher_accounts (
                    workspace_id, platform, account_key, account_type, external_account_reference
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, platform, account_key) DO NOTHING
                RETURNING id::text, workspace_id::text, platform, account_key,
                          account_type, external_account_reference
                """,
                (workspace_id, platform, account_key, account_type, external_account_reference),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT id::text, workspace_id::text, platform, account_key,
                           account_type, external_account_reference
                    FROM publisher_accounts
                    WHERE workspace_id = %s AND platform = %s AND account_key = %s
                    """,
                    (workspace_id, platform, account_key),
                )
                row = cursor.fetchone()
            if row is None:
                raise KeyError(account_key)
            identifier, found_workspace, found_platform, found_key, found_type, found_reference = row
            if (found_type, found_reference) != (account_type, external_account_reference):
                raise ImmutablePublicationConflict("publisher account differs from its canonical identity")
            if found_platform == "fixture":
                self._ensure_fixture_governance(cursor, identifier, found_workspace)
            return PersistedPublisherAccount(identifier, found_workspace, found_platform, found_key)

    def _create_request(
        self,
        ready_package_id: str,
        workspace_id: str,
        content_program_id: str,
        publisher_account_id: str,
        publication_approval_request_id: str,
        idempotency_key: str,
        platform: str,
        destination: str,
        locale: str,
        territory: str,
        visibility: str,
        capability_profile_version: int,
    ) -> PersistedPublicationRequest:
        publisher_id = _publisher_id_for_platform(platform)
        fingerprint = _fingerprint(
            {
                "ready_package_id": ready_package_id,
                "workspace_id": workspace_id,
                "content_program_id": content_program_id,
                "publisher_account_id": publisher_account_id,
                "publication_approval_request_id": publication_approval_request_id,
                "idempotency_key": idempotency_key,
                "publisher_id": publisher_id,
                "platform": platform,
                "destination": destination,
                "locale": locale,
                "territory": territory,
                "visibility": visibility,
                "capability_profile_version": capability_profile_version,
                "version": 1,
            }
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publication_requests (
                    tenant_id, workspace_id, content_program_id, ready_package_id,
                    publisher_account_id, publisher_capability_profile_id,
                    approval_request_id, publication_approval_request_id,
                    request_key, version,
                    publisher_id, platform, destination, locale, territory, visibility,
                    capability_profile_version, approval_reference, state, request_fingerprint,
                    disclosure_projection, policy_references, rights_references
                )
                SELECT ready.tenant_id, ready.workspace_id, ready.content_program_id, ready.id,
                       account.id, profile.id, ready.approval_request_id, approval.id, %s, 1,
                       %s, %s, %s, %s, %s, %s,
                       %s, 'publication-approval:' || approval.id::text, 'planned', %s,
                       jsonb_build_object(
                           'ready_package_id', ready.id::text,
                           'disclosure_id', ready.disclosure_id::text
                       ), ready.policy_versions, '[]'::jsonb
                FROM ready_to_publish_packages ready
                JOIN publisher_accounts account ON account.id = %s
                JOIN publisher_capability_profiles profile
                  ON profile.workspace_id = ready.workspace_id
                 AND profile.publisher_account_id = account.id
                 AND profile.publisher_id = %s
                 AND profile.profile_version = %s
                 AND profile.platform = %s
                 AND profile.audit_state = 'verified'
                 AND (profile.expires_at IS NULL OR profile.expires_at > CURRENT_TIMESTAMP)
                JOIN approval_requests approval ON approval.id = %s
                                                AND approval.workspace_id = ready.workspace_id
                                                AND approval.effect_type = 'publication'
                                                AND approval.status = 'approved'
                                                AND approval.request_context @> jsonb_build_object(
                                                    'ready_package_id', ready.id::text,
                                                    'publisher_account_id', account.id::text,
                                                    'platform', %s::text,
                                                    'destination', %s::text,
                                                    'locale', %s::text,
                                                    'territory', %s::text,
                                                    'visibility', %s::text,
                                                    'capability_profile_version', %s::integer
                                                )
                WHERE ready.id = %s
                  AND ready.workspace_id = %s
                  AND ready.content_program_id = %s
                  AND ready.approval_state = 'approved'
                  AND account.workspace_id = ready.workspace_id
                  AND account.platform = %s
                  AND account.status = 'active'
                ON CONFLICT (content_program_id, request_key, version) DO NOTHING
                RETURNING id::text, ready_package_id::text
                """,
                (
                    idempotency_key,
                    publisher_id,
                    platform,
                    destination,
                    locale,
                    territory,
                    visibility,
                    capability_profile_version,
                    fingerprint,
                    publisher_account_id,
                    publisher_id,
                    capability_profile_version,
                    platform,
                    publication_approval_request_id,
                    platform,
                    destination,
                    locale,
                    territory,
                    visibility,
                    capability_profile_version,
                    ready_package_id,
                    workspace_id,
                    content_program_id,
                    platform,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedPublicationRequest(*row)
            cursor.execute(
                """
                SELECT id::text, ready_package_id::text, workspace_id::text,
                       content_program_id::text, publisher_account_id::text,
                       publication_approval_request_id::text, request_fingerprint
                FROM publication_requests
                WHERE content_program_id = %s AND request_key = %s AND version = 1
                """,
                (content_program_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError(
                    "approved ready package, active publisher account, and publication approval are required"
                )
            (
                identifier,
                existing_ready,
                existing_workspace,
                existing_program,
                existing_account,
                existing_approval,
                existing_hash,
            ) = existing
            if (
                existing_ready,
                existing_workspace,
                existing_program,
                existing_account,
                existing_approval,
                existing_hash,
            ) != (
                ready_package_id,
                workspace_id,
                content_program_id,
                publisher_account_id,
                publication_approval_request_id,
                fingerprint,
            ):
                raise ImmutablePublicationConflict("publication request differs from its immutable version")
            return PersistedPublicationRequest(identifier, existing_ready)

    def _load_request(self, publication_request_id: str) -> PublicationRequest:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, workspace_id::text, content_program_id::text,
                       ready_package_id::text, publisher_account_id::text, platform,
                       destination, locale, territory, visibility,
                       capability_profile_version, request_key, publication_approval_request_id::text,
                       approval_reference, publisher_id
                FROM publication_requests
                WHERE id = %s
                """,
                (publication_request_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(publication_request_id)
        return _publication_request_from_row(row)

    def _load_scheduled_execution(self, publication_schedule_id: str) -> PersistedPublicationExecution:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT request.id::text, request.workspace_id::text,
                       request.content_program_id::text, request.ready_package_id::text,
                       request.publisher_account_id::text, request.platform,
                       request.destination, request.locale, request.territory,
                       request.visibility, request.capability_profile_version,
                       request.request_key, request.publication_approval_request_id::text,
                       request.approval_reference, request.publisher_id,
                       plan.id::text, plan.publisher_id, plan.publisher_version,
                       schedule.id::text, schedule.budget_id::text
                FROM publication_schedules schedule
                JOIN publication_requests request ON request.id = schedule.publication_request_id
                JOIN publication_plans plan ON plan.id = schedule.publication_plan_id
                                           AND plan.publication_request_id = request.id
                WHERE schedule.id = %s AND schedule.status = 'scheduled'
                  AND schedule.budget_id IS NOT NULL
                """,
                (publication_schedule_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError("publication schedule must name one immutable request, plan, and budget")
        return PersistedPublicationExecution(
            request=_publication_request_from_row(row[:15]),
            plan_id=row[15],
            publisher_id=row[16],
            publisher_version=row[17],
            schedule_id=row[18],
            budget_id=row[19],
        )

    def _load_current_authorization(
        self, publication_request_id: str
    ) -> CurrentPublicationAuthorization:
        with self._connect() as connection, connection.cursor() as cursor:
            with connection.transaction():
                self._lock_current_authorization_targets(cursor, publication_request_id)
                cursor.execute(CURRENT_PUBLICATION_AUTHORIZATION_SQL, (publication_request_id,))
                row = cursor.fetchone()
        return self._authorization_from_row(row, publication_request_id)

    @staticmethod
    def _lock_current_authorization_targets(cursor, publication_request_id: str) -> None:
        """Serialize the canonical publication target projection against target changes."""
        cursor.execute(
            "SELECT id FROM publication_requests WHERE id=%s FOR UPDATE",
            (publication_request_id,),
        )
        if cursor.fetchone() is None:
            return
        cursor.execute(
            """SELECT id, external_effect_id FROM publication_plans
               WHERE publication_request_id=%s ORDER BY version DESC LIMIT 1 FOR UPDATE""",
            (publication_request_id,),
        )
        plan = cursor.fetchone()
        if plan is None:
            return
        plan_id, external_effect_id = plan
        cursor.execute(
            """SELECT id FROM publication_attempts WHERE publication_plan_id=%s
               ORDER BY attempt_number DESC LIMIT 1 FOR UPDATE""",
            (plan_id,),
        )
        attempt = cursor.fetchone()
        attempt_id = attempt[0] if attempt is not None else None
        if external_effect_id is not None:
            cursor.execute(
                "SELECT id FROM external_effects WHERE id=%s FOR UPDATE",
                (external_effect_id,),
            )
            cursor.fetchone()
        if attempt_id is not None:
            cursor.execute(
                "SELECT id FROM remote_publication_receipts WHERE publication_attempt_id=%s FOR UPDATE",
                (attempt_id,),
            )
            receipt = cursor.fetchone()
            if receipt is not None:
                cursor.execute(
                    "SELECT id FROM publications WHERE remote_publication_receipt_id=%s FOR UPDATE",
                    (receipt[0],),
                )
                cursor.fetchone()
        cursor.execute(
            """SELECT job.id FROM jobs job JOIN approval_requests approval ON approval.job_id=job.id
               JOIN publication_requests request
                 ON request.publication_approval_request_id=approval.id
               WHERE request.id=%s FOR UPDATE OF job""",
            (publication_request_id,),
        )
        cursor.fetchone()

    def proposed_authorization(self, connection, payload: dict[str, Any]) -> CurrentPublicationAuthorization:
        """Read current native facts for an unpersisted proposal; reserve nothing.

        The existing native query is the sole source for connection/profile,
        disclosure, current policy and rights validation. The proposed row is
        scoped to the real native ready package/profile before projection.
        Its zero UUID is a proposal marker, never a persisted request identity.
        """
        from psycopg.rows import tuple_row
        from psycopg.types.json import Jsonb
        marker = "00000000-0000-0000-0000-000000000000"
        proposal = payload | {"id": marker, "request_key": payload["idempotency_key"],
            "publisher_id": "fixture-publisher", "approval_reference": "publication-approval:"+payload["publication_approval_request_id"]}
        prefix = """WITH proposal AS (SELECT %s::jsonb AS payload),
          publication_requests AS (
            SELECT (jsonb_populate_record(NULL::public.publication_requests,
                proposal.payload || jsonb_build_object(
                    'publisher_capability_profile_id', profile.id::text,
                    'policy_references', ready.policy_versions,
                    'rights_references', '[]'::jsonb))).*
            FROM proposal JOIN ready_to_publish_packages ready
              ON ready.id::text=proposal.payload->>'ready_package_id'
             AND ready.workspace_id::text=proposal.payload->>'workspace_id'
             AND ready.content_program_id::text=proposal.payload->>'content_program_id'
            JOIN publisher_capability_profiles profile
              ON profile.publisher_account_id::text=proposal.payload->>'publisher_account_id'
             AND profile.workspace_id=ready.workspace_id
             AND profile.publisher_id='fixture-publisher' AND profile.publisher_version='1'
             AND profile.platform='fixture' AND profile.profile_version=1
             AND profile.audit_state='verified'
             AND (profile.expires_at IS NULL OR profile.expires_at>clock_timestamp())
          ) """
        with connection.cursor(row_factory=tuple_row) as cursor:
            cursor.execute(prefix + CURRENT_PUBLICATION_AUTHORIZATION_SQL, (Jsonb(proposal), marker))
            row = cursor.fetchone()
        return self._authorization_from_row(row, marker)

    @staticmethod
    def _authorization_from_row(row, publication_request_id):
        if row is None:
            raise KeyError(publication_request_id)
        profile = PublisherCapabilityProfile.model_validate(row[24]) if row[24] is not None else None
        production_tagged = (
            len(row) > 27
            and isinstance(row[27], Mapping)
            and row[27].get("schema_version") == "salience.p1-effect-approval.v1"
        )
        production_policy = None
        approval_binding = None
        database_time = row[36] if len(row) > 36 else None
        if production_tagged:
            if (
                not isinstance(database_time, datetime)
                or database_time.tzinfo is None
                or database_time.utcoffset() is None
                or row[37] is not True
                or row[38] != "approved"
                or row[41] != "write"
            ):
                raise PermissionError("current tagged publication approval is invalid")
            policy_row = {
                "id": row[30],
                "workspace_id": row[31],
                "status": row[32],
                "document": row[33],
                "document_hash": row[34],
                "effective_at": row[35],
            }
            try:
                request = _publication_request_from_row(row[:15])
                production_policy = parse_policy_row(
                    policy_row,
                    workspace_id=UUID(row[1]),
                    database_time=database_time,
                )
                expected = _expected_production_approval(
                    request=request,
                    policy=production_policy,
                    approval_id=UUID(row[12]),
                    approval_workspace_id=UUID(row[42]),
                    approval_effect_type=row[41],
                    approval_job_id=UUID(row[39]) if row[39] is not None else None,
                    request_context=row[27],
                )
                approval_binding = parse_approval_row(
                    {
                        "id": row[12],
                        "workspace_id": row[42],
                        "policy_version_id": row[30],
                        "effect_type": row[41],
                        "status": row[38],
                        "request_context": row[27],
                        "job_id": row[39],
                        "decided_by": row[28],
                        "decided_at": row[29],
                    },
                    expected=expected,
                    database_time=database_time,
                )
            except (KeyError, TypeError, ValueError, PermissionError) as error:
                raise PermissionError("current tagged publication approval is invalid") from error
            if approval_binding.publication_request_id != UUID(row[0]):
                raise PermissionError("tagged publication request mismatch")
            if approval_binding.policy_document_hash != production_policy.document_hash:
                raise PermissionError("tagged publication policy hash mismatch")
        policy_document = row[33] if len(row) > 33 else None
        policy_expires_at = None
        if isinstance(policy_document, Mapping):
            raw_expiry = policy_document.get("expires_at")
            if isinstance(raw_expiry, str):
                try:
                    policy_expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
                except ValueError:
                    policy_expires_at = None
        return CurrentPublicationAuthorization(
            request=_publication_request_from_row(row[:15]),
            ready_package_approval_state=row[15],
            publication_approval_state=row[16],
            disclosure_status=row[17],
            account_workspace_id=row[18],
            account_type=row[19],
            account_status=row[20],
            connection_account_id=row[21],
            connection_status=row[22],
            connection_scopes=frozenset(row[23] or ()),
            profile=profile,
            policy_allowed=row[25],
            rights_allowed=row[26],
            policy_id=row[30] if len(row) > 30 else None,
            policy_document_hash=row[34] if len(row) > 34 else None,
            policy_status=row[32] if len(row) > 32 else None,
            policy_expires_at=policy_expires_at,
            production_policy=production_policy,
            approval_binding=approval_binding,
            database_time=database_time,
            budget_status=row[40] if len(row) > 40 else None,
            publication_plan_id=row[43] if len(row) > 43 else None,
            publication_plan_state=row[44] if len(row) > 44 else None,
            external_effect_id=row[45] if len(row) > 45 else None,
            external_effect_state=row[46] if len(row) > 46 else None,
            publication_attempt_id=row[47] if len(row) > 47 else None,
            publication_attempt_state=row[48] if len(row) > 48 else None,
            remote_receipt_id=row[49] if len(row) > 49 else None,
            remote_id=row[50] if len(row) > 50 else None,
            remote_receipt_state=row[51] if len(row) > 51 else None,
            publication_id=row[52] if len(row) > 52 else None,
            job_id=row[53] if len(row) > 53 else None,
            job_state=row[54] if len(row) > 54 else None,
            budget_reservation_id=row[55] if len(row) > 55 else None,
            publication_state=row[56] if len(row) > 56 else None,
        )

    @staticmethod
    def _ensure_fixture_governance(cursor: psycopg.Cursor[Any], account_id: str, workspace_id: str) -> None:
        secret_name = f"fixture-publisher-{account_id}"
        cursor.execute(
            """
            INSERT INTO secret_references (
                workspace_id, name, reference_uri, required_scopes, data_classification, status
            ) VALUES (%s, %s, %s, %s::jsonb, 'confidential', 'active')
            ON CONFLICT (workspace_id, name) DO NOTHING
            """,
            (
                workspace_id,
                secret_name,
                f"fixture://publisher/{account_id}",
                json.dumps(["publish:create"]),
            ),
        )
        cursor.execute(
            "SELECT id::text FROM secret_references WHERE workspace_id = %s AND name = %s",
            (workspace_id, secret_name),
        )
        secret_reference_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO publisher_connections (
                publisher_account_id, version, secret_reference_id, required_scopes, granted_scopes, status
            ) VALUES (%s, 1, %s, %s::jsonb, %s::jsonb, 'active')
            ON CONFLICT (publisher_account_id, version) DO NOTHING
            """,
            (
                account_id,
                secret_reference_id,
                json.dumps(["publish:create"]),
                json.dumps(["publish:create"]),
            ),
        )
        capability_facts = {
            "publisher_id": "fixture-publisher",
            "version": "1",
            "platform": "fixture",
            "account_types": ["creator"],
            "contract_compatibility": {"publication": "1.0"},
            "enabled": True,
            "audit_state": "verified",
            "granted_scopes": ["publish:create"],
            "supported_content_types": ["video"],
            "supported_visibilities": ["private"],
            "disclosure_support": True,
            "scheduling_support": True,
            "cancellation_support": True,
            "reconciliation_support": True,
            "quota_state": "available",
            "health_state": "healthy",
        }
        cursor.execute(
            """
            INSERT INTO publisher_capability_profiles (
                workspace_id, publisher_account_id, publisher_id, publisher_version,
                profile_version, platform, audit_state, observed_at, source_reference,
                capability_facts, compatibility
            ) VALUES (%s, %s, 'fixture-publisher', '1', 1, 'fixture', 'verified',
                      CURRENT_TIMESTAMP, 'fixture://publisher-capabilities/v1', %s::jsonb, %s::jsonb)
            ON CONFLICT (workspace_id, publisher_account_id, publisher_id, publisher_version, profile_version)
            DO NOTHING
            """,
            (
                workspace_id,
                account_id,
                json.dumps(capability_facts),
                json.dumps({"publication": "1.0"}),
            ),
        )

    def _create_plan(
        self,
        publication_request_id: str,
        version: int,
        publisher_id: str,
        publisher_version: str,
        external_effect_id: str | None,
        trace_id: str | None,
        span_id: str | None,
    ) -> PersistedPublicationPlan:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publication_plans (
                    publication_request_id, version, publisher_id, publisher_version,
                    external_effect_id, state, trace_id, span_id
                ) VALUES (%s, %s, %s, %s, %s, 'planned', %s, %s)
                ON CONFLICT (publication_request_id, version) DO NOTHING
                RETURNING id::text, publication_request_id::text
                """,
                (
                    publication_request_id,
                    version,
                    publisher_id,
                    publisher_version,
                    external_effect_id,
                    trace_id,
                    span_id,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedPublicationPlan(*row)
            cursor.execute(
                """
                SELECT id::text, publication_request_id::text, publisher_id, publisher_version,
                       external_effect_id::text
                FROM publication_plans
                WHERE publication_request_id = %s AND version = %s
                """,
                (publication_request_id, version),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError(publication_request_id)
            identifier, found_request, found_publisher, found_version, found_effect = existing
            if (found_publisher, found_version, found_effect) != (
                publisher_id,
                publisher_version,
                external_effect_id,
            ):
                raise ImmutablePublicationConflict("publication plan differs from its immutable version")
            return PersistedPublicationPlan(identifier, found_request)

    def _attach_reservation(self, publication_plan_id: str, budget_reservation_id: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE publication_plans
                SET budget_reservation_id = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND (budget_reservation_id IS NULL OR budget_reservation_id = %s)
                """,
                (budget_reservation_id, publication_plan_id, budget_reservation_id),
            )
            if cursor.rowcount != 1:
                raise ImmutablePublicationConflict("publication plan already has another budget reservation")

    def _attach_external_effect(self, publication_plan_id: str, external_effect_id: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE publication_plans
                SET external_effect_id = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND (external_effect_id IS NULL OR external_effect_id = %s)
                """,
                (external_effect_id, publication_plan_id, external_effect_id),
            )
            if cursor.rowcount != 1:
                raise ImmutablePublicationConflict("publication plan already has another external effect")

    def _create_attempt(
        self,
        publication_plan_id: str,
        attempt_number: int,
        idempotency_key: str,
    ) -> PersistedPublicationAttempt:
        fingerprint = _fingerprint(
            {
                "publication_plan_id": publication_plan_id,
                "attempt_number": attempt_number,
                "idempotency_key": idempotency_key,
            }
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publication_attempts (
                    publication_plan_id, attempt_number, idempotency_key, state, request_fingerprint
                ) VALUES (%s, %s, %s, 'planned', %s)
                ON CONFLICT (publication_plan_id, attempt_number) DO NOTHING
                RETURNING id::text, publication_plan_id::text
                """,
                (publication_plan_id, attempt_number, idempotency_key, fingerprint),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedPublicationAttempt(*row)
            cursor.execute(
                """
                SELECT id::text, publication_plan_id::text, attempt_number, idempotency_key,
                       request_fingerprint
                FROM publication_attempts
                WHERE publication_plan_id = %s
                  AND (attempt_number = %s OR idempotency_key = %s)
                """,
                (publication_plan_id, attempt_number, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError(publication_plan_id)
            identifier, found_plan, found_number, found_key, found_fingerprint = existing
            if (found_number, found_key, found_fingerprint) != (
                attempt_number,
                idempotency_key,
                fingerprint,
            ):
                raise ImmutablePublicationConflict("publication attempt differs from its idempotency key")
            return PersistedPublicationAttempt(identifier, found_plan)

    def _create_schedule(
        self,
        publication_request_id: str,
        publication_plan_id: str,
        job_schedule_id: str,
        budget_id: str,
        version: int,
        schedule_fingerprint: str,
    ) -> PersistedPublicationSchedule:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publication_schedules (
                    publication_request_id, publication_plan_id, job_schedule_id, budget_id, version,
                    scheduled_for, schedule_fingerprint, status
                )
                SELECT request.id, plan.id, schedule.id, budget.id, %s, CURRENT_TIMESTAMP, %s, 'scheduled'
                FROM publication_requests request
                JOIN publication_plans plan ON plan.publication_request_id = request.id
                JOIN job_schedules schedule ON schedule.id = %s
                                           AND schedule.workspace_id = request.workspace_id
                                           AND schedule.content_program_id = request.content_program_id
                                           AND schedule.job_type = 'governed_publication'
                                           AND schedule.payload = jsonb_build_object(
                                               'publication_request_id', request.id::text,
                                               'publication_plan_id', plan.id::text,
                                               'budget_id', %s::text,
                                               'schedule_version', %s::integer,
                                               'contract_version', 'PublicationSchedule@v1'
                                           )
                JOIN budgets budget ON budget.id = %s
                                  AND budget.workspace_id = request.workspace_id
                                  AND budget.content_program_id = request.content_program_id
                                  AND budget.status = 'active'
                WHERE request.id = %s AND plan.id = %s
                ON CONFLICT (publication_request_id, version) DO NOTHING
                RETURNING id::text, publication_request_id::text, publication_plan_id::text,
                          job_schedule_id::text, version
                """,
                (
                    version,
                    schedule_fingerprint,
                    job_schedule_id,
                    budget_id,
                    version,
                    budget_id,
                    publication_request_id,
                    publication_plan_id,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedPublicationSchedule(*row)
            cursor.execute(
                """
                SELECT id::text, publication_request_id::text, publication_plan_id::text,
                       job_schedule_id::text, budget_id::text, version, schedule_fingerprint
                FROM publication_schedules
                WHERE publication_request_id = %s AND version = %s
                """,
                (publication_request_id, version),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError("job schedule payload or publication request, plan, and budget are required")
            (
                identifier,
                found_request,
                found_plan,
                found_job_schedule,
                found_budget,
                found_version,
                found_hash,
            ) = existing
            if (found_request, found_plan, found_job_schedule, found_budget, found_version, found_hash) != (
                publication_request_id,
                publication_plan_id,
                job_schedule_id,
                budget_id,
                version,
                schedule_fingerprint,
            ):
                raise ImmutablePublicationConflict("publication schedule differs from its immutable version")
            return PersistedPublicationSchedule(
                identifier, found_request, found_plan, found_job_schedule, found_version
            )

    def _record_remote_receipt(
        self,
        publication_attempt_id: str,
        publisher_id: str,
        remote_id: str,
        state: str,
        safe_metadata_hash: str,
        remote_url: str | None,
    ) -> PersistedRemotePublicationReceipt:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO remote_publication_receipts (
                    publication_attempt_id, publisher_id, remote_id, state, remote_url,
                    safe_metadata_hash
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id::text, publication_attempt_id::text, remote_id
                """,
                (
                    publication_attempt_id,
                    publisher_id,
                    remote_id,
                    state,
                    remote_url,
                    safe_metadata_hash,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedRemotePublicationReceipt(*row)
            cursor.execute(
                """
                SELECT id::text, publication_attempt_id::text, publisher_id, remote_id,
                       state, remote_url, safe_metadata_hash
                FROM remote_publication_receipts
                WHERE publication_attempt_id = %s OR (publisher_id = %s AND remote_id = %s)
                """,
                (publication_attempt_id, publisher_id, remote_id),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError(publication_attempt_id)
            identifier, found_attempt, found_publisher, found_remote, found_state, found_url, found_hash = existing
            if (found_attempt, found_publisher, found_remote, found_state, found_url, found_hash) != (
                publication_attempt_id,
                publisher_id,
                remote_id,
                state,
                remote_url,
                safe_metadata_hash,
            ):
                raise ImmutablePublicationConflict("remote publication receipt differs from its immutable fact")
            return PersistedRemotePublicationReceipt(identifier, found_attempt, found_remote)

    def _record_status_event(
        self,
        publication_attempt_id: str,
        state: str,
        source: str,
        safe_payload_hash: str,
        trace_id: str | None,
        span_id: str | None,
    ) -> PersistedPublicationStatusEvent:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (publication_attempt_id,))
            cursor.execute(
                """
                INSERT INTO publication_status_events (
                    publication_attempt_id, sequence_no, state, source, safe_payload_hash,
                    trace_id, span_id
                ) SELECT %s, COALESCE(MAX(sequence_no), 0) + 1, %s, %s, %s, %s, %s
                  FROM publication_status_events
                 WHERE publication_attempt_id = %s
                ON CONFLICT (publication_attempt_id, source, safe_payload_hash) DO NOTHING
                RETURNING id::text, publication_attempt_id::text, state
                """,
                (
                    publication_attempt_id,
                    state,
                    source,
                    safe_payload_hash,
                    trace_id,
                    span_id,
                    publication_attempt_id,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedPublicationStatusEvent(*row)
            cursor.execute(
                """
                SELECT id::text, publication_attempt_id::text, state
                FROM publication_status_events
                WHERE publication_attempt_id = %s AND source = %s AND safe_payload_hash = %s
                """,
                (publication_attempt_id, source, safe_payload_hash),
            )
            existing = cursor.fetchone()
            if existing is None or existing[2] != state:
                raise ImmutablePublicationConflict("publication status event differs from existing event")
            return PersistedPublicationStatusEvent(*existing)

    def _record_verified_webhook(
        self,
        publication_attempt_id: str,
        status_event_id: str,
        publisher_id: str,
        delivery_identity: str,
        safe_payload_hash: str,
        state: str,
        trace_id: str | None,
        span_id: str | None,
    ) -> PersistedPublisherWebhookReceipt:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publisher_webhook_receipts (
                    publication_attempt_id, publication_status_event_id, publisher_id,
                    delivery_identity, safe_payload_hash, signature_verified, state, trace_id, span_id
                ) VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id::text, publication_attempt_id::text, state
                """,
                (
                    publication_attempt_id,
                    status_event_id,
                    publisher_id,
                    delivery_identity,
                    safe_payload_hash,
                    state,
                    trace_id,
                    span_id,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedPublisherWebhookReceipt(*row)
            cursor.execute(
                """
                SELECT id::text, publication_attempt_id::text, publication_status_event_id::text,
                       publisher_id, delivery_identity, safe_payload_hash, state
                FROM publisher_webhook_receipts
                WHERE publisher_id = %s AND delivery_identity = %s
                   OR publication_attempt_id = %s AND safe_payload_hash = %s
                """,
                (publisher_id, delivery_identity, publication_attempt_id, safe_payload_hash),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError(delivery_identity)
            identifier, found_attempt, found_event, found_publisher, found_delivery, found_hash, found_state = existing
            if (found_attempt, found_event, found_publisher, found_delivery, found_hash, found_state) != (
                publication_attempt_id,
                status_event_id,
                publisher_id,
                delivery_identity,
                safe_payload_hash,
                state,
            ):
                raise ImmutablePublicationConflict("publisher webhook receipt differs from verified delivery")
            return PersistedPublisherWebhookReceipt(identifier, found_attempt, found_state)

    def _attempt_for_remote(self, publisher_id: str, remote_id: str) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT publication_attempt_id::text
                FROM remote_publication_receipts
                WHERE publisher_id = %s AND remote_id = %s
                """,
                (publisher_id, remote_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(remote_id)
            return row[0]

    def _record_publication(
        self,
        publication_request_id: str,
        remote_receipt_id: str,
        state: str,
        trace_id: str | None,
        span_id: str | None,
    ) -> PersistedPublication:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publications (
                    publication_request_id, remote_publication_receipt_id, state, published_at,
                    trace_id, span_id
                ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s)
                ON CONFLICT (publication_request_id) DO NOTHING
                RETURNING id::text, publication_request_id::text, remote_publication_receipt_id::text
                """,
                (publication_request_id, remote_receipt_id, state, trace_id, span_id),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedPublication(*row)
            cursor.execute(
                """
                SELECT id::text, publication_request_id::text, remote_publication_receipt_id::text, state
                FROM publications WHERE publication_request_id = %s
                """,
                (publication_request_id,),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError(publication_request_id)
            identifier, found_request, found_receipt, found_state = existing
            if (found_receipt, found_state) != (remote_receipt_id, state):
                raise ImmutablePublicationConflict("publication differs from immutable receipt reference")
            return PersistedPublication(identifier, found_request, found_receipt)


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _publisher_id_for_platform(platform: str) -> str:
    return "fixture-publisher" if platform == "fixture" else f"{platform}-publisher"


def _publication_request_from_row(row: tuple[Any, ...]) -> PublicationRequest:
    return PublicationRequest(
        id=row[0],
        workspace_id=row[1],
        content_program_id=row[2],
        ready_package_id=row[3],
        publisher_account_id=row[4],
        platform=row[5],
        destination=row[6],
        locale=row[7],
        territory=row[8],
        visibility=row[9],
        capability_profile_version=row[10],
        idempotency_key=row[11],
        publication_approval_request_id=row[12],
        approval_reference=row[13],
        publisher_id=row[14],
    )
