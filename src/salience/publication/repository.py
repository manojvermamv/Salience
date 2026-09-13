"""Canonical, provider-neutral governed publication persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

import psycopg


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


@dataclass(frozen=True)
class PersistedPublication:
    id: str
    publication_request_id: str
    remote_receipt_id: str


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
        idempotency_key: str,
    ) -> PersistedPublicationRequest:
        return await asyncio.to_thread(
            self._create_request,
            ready_package_id,
            workspace_id,
            content_program_id,
            publisher_account_id,
            idempotency_key,
        )

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
            return PersistedPublisherAccount(identifier, found_workspace, found_platform, found_key)

    def _create_request(
        self,
        ready_package_id: str,
        workspace_id: str,
        content_program_id: str,
        publisher_account_id: str,
        idempotency_key: str,
    ) -> PersistedPublicationRequest:
        fingerprint = _fingerprint(
            {
                "ready_package_id": ready_package_id,
                "workspace_id": workspace_id,
                "content_program_id": content_program_id,
                "publisher_account_id": publisher_account_id,
                "idempotency_key": idempotency_key,
                "version": 1,
            }
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publication_requests (
                    tenant_id, workspace_id, content_program_id, ready_package_id,
                    publisher_account_id, approval_request_id, request_key, version,
                    publisher_id, platform, destination, locale, territory, visibility,
                    capability_profile_version, approval_reference, state, request_fingerprint,
                    disclosure_projection, policy_references, rights_references
                )
                SELECT ready.tenant_id, ready.workspace_id, ready.content_program_id, ready.id,
                       account.id, ready.approval_request_id, %s, 1,
                       account.platform, account.platform,
                       'fixture://' || account.id::text, 'en', 'global', 'private',
                       1, 'ready-package:' || ready.id::text, 'planned', %s,
                       jsonb_build_object(
                           'ready_package_id', ready.id::text,
                           'disclosure_id', ready.disclosure_id::text
                       ), ready.policy_versions, '[]'::jsonb
                FROM ready_to_publish_packages ready
                JOIN publisher_accounts account ON account.id = %s
                WHERE ready.id = %s
                  AND ready.workspace_id = %s
                  AND ready.content_program_id = %s
                  AND ready.approval_state = 'approved'
                  AND account.workspace_id = ready.workspace_id
                  AND account.status = 'active'
                ON CONFLICT (content_program_id, request_key, version) DO NOTHING
                RETURNING id::text, ready_package_id::text
                """,
                (
                    idempotency_key,
                    fingerprint,
                    publisher_account_id,
                    ready_package_id,
                    workspace_id,
                    content_program_id,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return PersistedPublicationRequest(*row)
            cursor.execute(
                """
                SELECT id::text, ready_package_id::text, workspace_id::text,
                       content_program_id::text, publisher_account_id::text, request_fingerprint
                FROM publication_requests
                WHERE content_program_id = %s AND request_key = %s AND version = 1
                """,
                (content_program_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise KeyError("approved ready package and active publisher account are required")
            identifier, existing_ready, existing_workspace, existing_program, existing_account, existing_hash = existing
            if (
                existing_ready,
                existing_workspace,
                existing_program,
                existing_account,
                existing_hash,
            ) != (
                ready_package_id,
                workspace_id,
                content_program_id,
                publisher_account_id,
                fingerprint,
            ):
                raise ImmutablePublicationConflict("publication request differs from its immutable version")
            return PersistedPublicationRequest(identifier, existing_ready)

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
