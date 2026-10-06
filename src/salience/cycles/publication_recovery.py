"""Immutable recovery holds for scoped legacy publication evidence."""

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


PublicationRecoveryReason = Literal[
    "expired_permit_receipt",
    "published_during_cancel",
]


@dataclass(frozen=True)
class PublicationRecoveryHoldView:
    id: UUID
    workspace_id: UUID
    operation_id: UUID
    goal_id: UUID
    intent_id: UUID
    cycle_id: UUID
    context_id: UUID
    job_id: UUID
    publication_attempt_id: UUID
    owner_id: UUID
    reason: PublicationRecoveryReason
    state: Literal["held"]
    execution_deadline: datetime
    permit_expires_at: datetime
    created_at: datetime


class PublicationRecoveryConflictError(ValueError):
    """An operation already has a different publication recovery hold."""


_SNAPSHOT_SELECT = """jsonb_build_object(
    'dispatch', to_jsonb(binding),
    'context', to_jsonb(context),
    'cycle', to_jsonb(cycle),
    'job', to_jsonb(job),
    'permit_claim', to_jsonb(claim),
    'permit', to_jsonb(permit),
    'fixture_receipt', to_jsonb(receipt_row),
    'fixture_progress', to_jsonb(progress),
    'publication_request', to_jsonb(pub_request),
    'publication_plan', to_jsonb(plan),
    'publication_attempt', to_jsonb(publication_attempt_row.*),
    'external_effect', COALESCE(to_jsonb(effect), 'null'::jsonb),
    'budget_reservation', COALESCE(to_jsonb(reservation), 'null'::jsonb),
    'publication_status_events', COALESCE((
        SELECT jsonb_agg(to_jsonb(status_event)
                         ORDER BY status_event.sequence_no, status_event.id)
        FROM public.publication_status_events status_event
        WHERE status_event.publication_attempt_id=publication_attempt_row.id
    ), '[]'::jsonb),
    'cycle_events', COALESCE((
        SELECT jsonb_agg(to_jsonb(cycle_event)
                         ORDER BY cycle_event.created_at, cycle_event.id)
        FROM public.v4_cycle_events cycle_event
        WHERE cycle_event.cycle_id=cycle.id
    ), '[]'::jsonb),
    'cycle_outbox', COALESCE((
        SELECT jsonb_agg(to_jsonb(outbox)
                         ORDER BY outbox.sequence, outbox.id)
        FROM public.v4_cycle_outbox outbox
        WHERE outbox.cycle_id=cycle.id
    ), '[]'::jsonb)
)"""

_EVIDENCE_FROM = """FROM public.v4_legacy_dispatches binding
JOIN public.v4_cycles cycle
  ON cycle.id=binding.cycle_id AND cycle.intent_id=binding.intent_id
 AND cycle.context_id=binding.context_id AND cycle.operation_id=binding.operation_id
JOIN public.v4_cycle_intents intent
  ON intent.id=binding.intent_id AND intent.goal_id=binding.goal_id
JOIN public.v4_goals goal
  ON goal.id=binding.goal_id AND goal.workspace_id=binding.workspace_id
JOIN public.v4_run_contexts context
  ON context.id=binding.context_id AND context.cycle_id=cycle.id
JOIN public.identity_subjects owner
  ON owner.id=binding.actor_id AND owner.workspace_id=binding.workspace_id
JOIN public.jobs job
  ON job.id=binding.job_id AND job.workspace_id=binding.workspace_id
JOIN public.v4_permit_claims claim
  ON claim.cycle_id=cycle.id AND claim.operation_id=binding.operation_id
 AND claim.context_id=context.id
JOIN public.v4_dispatch_permits permit
  ON permit.id=claim.current_permit_id AND permit.claim_id=claim.id
JOIN public.v4_legacy_publication_fixture_receipts receipt_row
  ON receipt_row.operation_id=binding.operation_id
 AND receipt_row.workspace_id=binding.workspace_id
JOIN public.v4_legacy_publication_fixture_progress progress
  ON progress.operation_id=receipt_row.operation_id
JOIN public.publication_requests pub_request
  ON pub_request.id=(receipt_row.request->>'id')::uuid
 AND receipt_row.receipt->>'publication_attempt_id'=pub_request.id::text
JOIN public.publication_plans plan
  ON plan.publication_request_id=pub_request.id
JOIN public.publication_attempts publication_attempt_row
  ON publication_attempt_row.publication_plan_id=plan.id
 AND publication_attempt_row.idempotency_key=receipt_row.idempotency_key||':attempt:1'
LEFT JOIN public.external_effects effect
  ON effect.id=plan.external_effect_id
LEFT JOIN public.budget_reservations reservation
  ON reservation.id=plan.budget_reservation_id"""

_LOCK_EVIDENCE_SQL = """SELECT plan.external_effect_id, plan.budget_reservation_id
FROM public.v4_legacy_dispatches binding
JOIN public.v4_cycles cycle
  ON cycle.id=binding.cycle_id AND cycle.intent_id=binding.intent_id
 AND cycle.context_id=binding.context_id AND cycle.operation_id=binding.operation_id
JOIN public.v4_cycle_intents intent
  ON intent.id=binding.intent_id AND intent.goal_id=binding.goal_id
JOIN public.v4_goals goal
  ON goal.id=binding.goal_id AND goal.workspace_id=binding.workspace_id
JOIN public.v4_run_contexts context
  ON context.id=binding.context_id AND context.cycle_id=cycle.id
JOIN public.identity_subjects owner
  ON owner.id=binding.actor_id AND owner.workspace_id=binding.workspace_id
JOIN public.jobs job
  ON job.id=binding.job_id AND job.workspace_id=binding.workspace_id
JOIN public.v4_permit_claims claim
  ON claim.cycle_id=cycle.id AND claim.operation_id=binding.operation_id
 AND claim.context_id=context.id
JOIN public.v4_dispatch_permits permit
  ON permit.id=claim.current_permit_id AND permit.claim_id=claim.id
JOIN public.v4_legacy_publication_fixture_receipts receipt_row
  ON receipt_row.operation_id=binding.operation_id
 AND receipt_row.workspace_id=binding.workspace_id
JOIN public.v4_legacy_publication_fixture_progress progress
  ON progress.operation_id=receipt_row.operation_id
JOIN public.publication_requests pub_request
  ON pub_request.id=(receipt_row.request->>'id')::uuid
 AND receipt_row.receipt->>'publication_attempt_id'=pub_request.id::text
JOIN public.publication_plans plan
  ON plan.publication_request_id=pub_request.id
JOIN public.publication_attempts publication_attempt_row
  ON publication_attempt_row.publication_plan_id=plan.id
 AND publication_attempt_row.idempotency_key=receipt_row.idempotency_key||':attempt:1'
WHERE binding.operation_id=%s AND binding.workspace_id=%s
  AND binding.stage='publication'
FOR UPDATE OF binding, cycle, intent, goal, context, owner, job, claim,
              permit, receipt_row, progress, pub_request, plan, publication_attempt_row"""

_CANDIDATE_SQL = f"""WITH candidate AS (
    SELECT binding.operation_id,
           binding.workspace_id,
           binding.goal_id,
           binding.intent_id,
           binding.cycle_id,
           binding.context_id,
           binding.job_id,
           publication_attempt_row.id AS publication_attempt_id,
           claim.current_permit_id AS permit_id,
           binding.actor_id AS owner_id,
           (context.payload->>'execution_deadline')::timestamptz AS execution_deadline,
           permit.expires_at AS permit_expires_at,
           receipt_row.fingerprint AS request_fingerprint,
           encode(public.digest(receipt_row.receipt::text, 'sha256'), 'hex')
               AS receipt_fingerprint,
           receipt_row.remote_id,
           progress.state AS remote_state,
           %s::text AS reason,
           'held'::text AS state,
           {_SNAPSHOT_SELECT} AS liability_snapshot
    {_EVIDENCE_FROM}
    WHERE binding.operation_id=%s
      AND binding.workspace_id=%s
      AND binding.stage='publication'
      AND binding.payload=job.input_payload
      AND job.job_type='governed_publication'
      AND job.actor_kind='identity'
      AND job.actor_id=binding.actor_id::text
      AND job.state='running'
      AND (context.payload->>'subject_id')::uuid=binding.actor_id
      AND claim.state IN ('claimed','unknown')
      AND claim.current_permit_id=permit.id
      AND permit.subject_id=binding.actor_id
      AND permit.request->>'operation_id'=binding.operation_id::text
      AND permit.request->>'context_id'=context.id::text
      AND receipt_row.idempotency_key='v4-legacy:'||binding.operation_id::text
      AND receipt_row.fingerprint=
          encode(public.digest(receipt_row.request::text, 'sha256'), 'hex')
      AND receipt_row.request->>'workspace_id'=binding.workspace_id::text
      AND receipt_row.request->>'content_program_id'=binding.content_program_id::text
      AND pub_request.workspace_id=binding.workspace_id
      AND pub_request.content_program_id=binding.content_program_id
      AND pub_request.request_key=receipt_row.idempotency_key
      AND pub_request.publisher_account_id::text=binding.payload->>'publisher_account_id'
      AND pub_request.ready_package_id::text=binding.payload->>'ready_package_id'
      AND receipt_row.receipt->>'state'='accepted'
      AND receipt_row.receipt->>'remote_id'=receipt_row.remote_id
      AND receipt_row.remote_id='fixture-v4-publication:'||binding.operation_id::text
      AND receipt_row.receipt->>'publisher_id'='fixture-publisher'
      AND (
        (%s='expired_permit_receipt'
          AND permit.expires_at<=clock_timestamp()
          AND progress.state IN ('accepted','processing')
          AND cycle.state<>'closed')
        OR
        (%s='published_during_cancel'
          AND cycle.state='closed' AND cycle.disposition='cancelled'
          AND progress.state='published')
      )
)
SELECT candidate.* FROM candidate"""

_REPLAY_FIELDS = (
    "operation_id",
    "workspace_id",
    "goal_id",
    "intent_id",
    "cycle_id",
    "context_id",
    "job_id",
    "publication_attempt_id",
    "permit_id",
    "owner_id",
    "execution_deadline",
    "permit_expires_at",
    "request_fingerprint",
    "receipt_fingerprint",
    "liability_fingerprint",
    "remote_id",
    "remote_state",
    "reason",
    "state",
    "liability_snapshot",
)

_HOLD_COLUMNS = """id, operation_id, workspace_id, goal_id, intent_id, cycle_id,
context_id, job_id, publication_attempt_id, permit_id, owner_id,
execution_deadline, permit_expires_at, request_fingerprint,
receipt_fingerprint, liability_fingerprint, remote_id, remote_state,
reason, state, liability_snapshot, created_at"""

_ROLLBACK_REASON_ORDER: tuple[PublicationRecoveryReason, ...] = (
    "expired_permit_receipt",
    "published_during_cancel",
)


def _fetchone(connection, statement, params=()):
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(statement, params)
        return cursor.fetchone()


def _view(row) -> PublicationRecoveryHoldView:
    return PublicationRecoveryHoldView(
        id=row["id"],
        workspace_id=row["workspace_id"],
        operation_id=row["operation_id"],
        goal_id=row["goal_id"],
        intent_id=row["intent_id"],
        cycle_id=row["cycle_id"],
        context_id=row["context_id"],
        job_id=row["job_id"],
        publication_attempt_id=row["publication_attempt_id"],
        owner_id=row["owner_id"],
        reason=row["reason"],
        state=row["state"],
        execution_deadline=row["execution_deadline"],
        permit_expires_at=row["permit_expires_at"],
        created_at=row["created_at"],
    )


def load_publication_recovery_hold(
    connection, *, workspace_id: UUID, operation_id: UUID
) -> PublicationRecoveryHoldView | None:
    """Load only the stable public projection for the requested scope."""
    row = _fetchone(
        connection,
        f"""SELECT {_HOLD_COLUMNS}
            FROM public.v4_legacy_publication_recovery_holds
            WHERE workspace_id=%s AND operation_id=%s""",
        (UUID(str(workspace_id)), UUID(str(operation_id))),
    )
    return _view(row) if row else None


def _same_evidence(stored, candidate) -> bool:
    return all(stored[field] == candidate[field] for field in _REPLAY_FIELDS)


def _lock_candidate_rows(connection, workspace_id, operation_id):
    locked = _fetchone(
        connection,
        _LOCK_EVIDENCE_SQL,
        (operation_id, workspace_id),
    )
    if not locked:
        return
    for table, key in (
        ("external_effects", "external_effect_id"),
        ("budget_reservations", "budget_reservation_id"),
    ):
        row_id = locked[key]
        if row_id is not None:
            _fetchone(
                connection,
                f"SELECT id FROM public.{table} WHERE id=%s FOR UPDATE",
                (row_id,),
            )


def record_publication_recovery_hold(
    database_url: str,
    *,
    workspace_id: UUID,
    operation_id: UUID,
    reason: PublicationRecoveryReason,
) -> PublicationRecoveryHoldView:
    """Record a scoped publication hold, or return its exact immutable replay."""
    if reason not in ("expired_permit_receipt", "published_during_cancel"):
        raise ValueError("known publication recovery reason required")
    workspace_id = UUID(str(workspace_id))
    operation_id = UUID(str(operation_id))

    with psycopg.connect(
        database_url, row_factory=dict_row, connect_timeout=3
    ) as connection:
        connection.execute("SET LOCAL statement_timeout='3s'")
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("publication-recovery-hold:" + str(operation_id),),
        )
        stored = _fetchone(
            connection,
            f"""SELECT {_HOLD_COLUMNS}
                FROM public.v4_legacy_publication_recovery_holds
                WHERE operation_id=%s""",
            (operation_id,),
        )
        if stored and (
            stored["workspace_id"] != workspace_id or stored["reason"] != reason
        ):
            raise PublicationRecoveryConflictError(
                "publication recovery operation already has a different hold binding"
            )

        _lock_candidate_rows(connection, workspace_id, operation_id)
        candidate = _fetchone(
            connection,
            _CANDIDATE_SQL,
            (reason, operation_id, workspace_id, reason, reason),
        )
        if candidate is None:
            if stored:
                raise PublicationRecoveryConflictError(
                    "publication recovery evidence changed after the original hold"
                )
            raise PermissionError(
                "original scoped publication evidence and recovery reason required"
            )

        snapshot_parameter = Jsonb(candidate["liability_snapshot"])
        candidate["liability_fingerprint"] = _fetchone(
            connection,
            """SELECT encode(
                    public.digest(%s::jsonb::text, 'sha256'), 'hex'
                ) AS fingerprint""",
            (snapshot_parameter,),
        )["fingerprint"]

        if stored:
            if not _same_evidence(stored, candidate):
                raise PublicationRecoveryConflictError(
                    "publication recovery evidence conflicts with the original hold"
                )
            return _view(stored)

        insert_values = {field: candidate[field] for field in _REPLAY_FIELDS}
        insert_values["liability_snapshot"] = snapshot_parameter
        inserted = _fetchone(
            connection,
            f"""INSERT INTO public.v4_legacy_publication_recovery_holds (
                    operation_id, workspace_id, goal_id, intent_id, cycle_id,
                    context_id, job_id, publication_attempt_id, permit_id, owner_id,
                    execution_deadline, permit_expires_at, request_fingerprint,
                    receipt_fingerprint, liability_fingerprint, remote_id,
                    remote_state, reason, state, liability_snapshot
                ) VALUES (
                    %(operation_id)s, %(workspace_id)s, %(goal_id)s, %(intent_id)s,
                    %(cycle_id)s, %(context_id)s, %(job_id)s,
                    %(publication_attempt_id)s, %(permit_id)s, %(owner_id)s,
                    %(execution_deadline)s, %(permit_expires_at)s,
                    %(request_fingerprint)s, %(receipt_fingerprint)s,
                    %(liability_fingerprint)s, %(remote_id)s, %(remote_state)s,
                    %(reason)s, %(state)s, %(liability_snapshot)s
                ) ON CONFLICT (operation_id) DO NOTHING
                RETURNING {_HOLD_COLUMNS}""",
            insert_values,
        )
        if inserted:
            return _view(inserted)

        raced = _fetchone(
            connection,
            f"""SELECT {_HOLD_COLUMNS}
                FROM public.v4_legacy_publication_recovery_holds
                WHERE operation_id=%s""",
            (operation_id,),
        )
        if (
            raced is None
            or raced["workspace_id"] != workspace_id
            or raced["reason"] != reason
            or not _same_evidence(raced, candidate)
        ):
            raise PublicationRecoveryConflictError(
                "concurrent publication recovery hold has different evidence"
            )
        return _view(raced)


def verify_publication_recovery_rollback(
    database_url: str,
    *,
    workspace_id: UUID,
    operation_ids: Mapping[PublicationRecoveryReason, UUID],
) -> tuple[PublicationRecoveryHoldView, PublicationRecoveryHoldView]:
    """Verify both retained rollback holds and their unchanged liabilities."""
    if not isinstance(operation_ids, Mapping) or set(operation_ids) != set(
        _ROLLBACK_REASON_ORDER
    ):
        raise ValueError("exactly both publication recovery reasons are required")

    workspace_id = UUID(str(workspace_id))
    normalized_operation_ids = {}
    for reason in _ROLLBACK_REASON_ORDER:
        try:
            normalized_operation_ids[reason] = UUID(str(operation_ids[reason]))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("valid operation IDs are required") from None
    if len(set(normalized_operation_ids.values())) != len(_ROLLBACK_REASON_ORDER):
        raise ValueError("publication recovery operation IDs must be distinct")

    verified = []
    with psycopg.connect(
        database_url, row_factory=dict_row, connect_timeout=3
    ) as connection:
        connection.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        connection.execute("SET LOCAL statement_timeout='3s'")

        for reason in _ROLLBACK_REASON_ORDER:
            operation_id = normalized_operation_ids[reason]
            stored = _fetchone(
                connection,
                f"""SELECT {_HOLD_COLUMNS}
                    FROM public.v4_legacy_publication_recovery_holds
                    WHERE workspace_id=%s AND operation_id=%s""",
                (workspace_id, operation_id),
            )
            if stored is None:
                raise PublicationRecoveryConflictError(
                    "both immutable publication recovery holds are required"
                )

            candidate = _fetchone(
                connection,
                _CANDIDATE_SQL,
                (reason, operation_id, workspace_id, reason, reason),
            )
            if candidate is None:
                raise PublicationRecoveryConflictError(
                    "publication recovery evidence changed after the original hold"
                )

            candidate_snapshot = Jsonb(candidate["liability_snapshot"])
            candidate["liability_fingerprint"] = _fetchone(
                connection,
                """SELECT encode(
                        public.digest(%s::jsonb::text, 'sha256'), 'hex'
                    ) AS fingerprint""",
                (candidate_snapshot,),
            )["fingerprint"]
            if not _same_evidence(stored, candidate):
                raise PublicationRecoveryConflictError(
                    "publication recovery evidence conflicts with the original hold"
                )
            verified.append(_view(stored))

    return verified[0], verified[1]
