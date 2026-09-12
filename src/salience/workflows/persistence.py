import asyncio
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg

from salience.observability.tracing import TraceContext


@dataclass(frozen=True)
class CanonicalRun:
    workspace_id: UUID
    content_program_id: UUID
    job_id: UUID
    workflow_run_id: str
    trace_context: TraceContext


@dataclass(frozen=True)
class CanonicalCounts:
    checkpoint_count: int
    effect_count: int
    audit_count: int
    provenance_count: int


class CanonicalJobStore:
    """Synchronous Psycopg operations dispatched off Temporal's activity event loop."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def create_run(
        self, *, workflow_run_id: str, task_queue: str, idempotency_key: str
    ) -> CanonicalRun:
        return await asyncio.to_thread(
            self._create_run, workflow_run_id, task_queue, idempotency_key
        )

    async def checkpoint(self, run: CanonicalRun, checkpoint_name: str) -> None:
        await asyncio.to_thread(self._checkpoint, run, checkpoint_name)

    async def plan_effect(self, run: CanonicalRun, idempotency_key: str) -> None:
        await asyncio.to_thread(self._plan_effect, run, idempotency_key)

    async def complete_effect(
        self, run: CanonicalRun, *, idempotency_key: str, external_id: str, reconciled: bool
    ) -> None:
        await asyncio.to_thread(
            self._complete_effect, run, idempotency_key, external_id, reconciled
        )

    async def counts(self, run: CanonicalRun) -> CanonicalCounts:
        return await asyncio.to_thread(self._counts, run)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._database_url)

    def _create_run(
        self, workflow_run_id: str, task_queue: str, idempotency_key: str
    ) -> CanonicalRun:
        workspace_id, content_program_id, job_id = uuid4(), uuid4(), uuid4()
        trace_context = TraceContext.new_root()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO workspaces (id, slug, display_name) VALUES (%s, %s, %s)",
                (workspace_id, f"restart-workspace-{workspace_id}", "Restart reconciliation contract"),
            )
            cursor.execute(
                """
                INSERT INTO content_programs (id, workspace_id, slug, name, niche)
                VALUES (%s, %s, 'durable-dummy', 'Durable dummy', 'test fixture')
                """,
                (content_program_id, workspace_id),
            )
            cursor.execute(
                """
                INSERT INTO jobs (
                    id, workspace_id, content_program_id, job_type, state, workflow_run_id,
                    task_queue, idempotency_key, input_payload, retry_policy, trace_id, span_id, dry_run
                ) VALUES (
                    %s, %s, %s, 'durable_dummy', 'running', %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s, %s, FALSE
                )
                """,
                (
                    job_id,
                    workspace_id,
                    content_program_id,
                    workflow_run_id,
                    task_queue,
                    idempotency_key,
                    json.dumps({"idempotency_key": idempotency_key}),
                    json.dumps({"maximum_attempts": 3}),
                    trace_context.trace_id,
                    trace_context.span_id,
                ),
            )
            self._insert_audit(
                cursor, workspace_id, job_id, workflow_run_id, trace_context, "job.started",
                "allowed", {"task_queue": task_queue}
            )
        return CanonicalRun(
            workspace_id, content_program_id, job_id, workflow_run_id, trace_context
        )

    def _checkpoint(self, run: CanonicalRun, checkpoint_name: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM job_checkpoints WHERE job_id = %s",
                (run.job_id,),
            )
            sequence_no = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO job_checkpoints (
                    job_id, sequence_no, state, checkpoint_payload, trace_id, span_id
                ) VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    run.job_id,
                    sequence_no,
                    checkpoint_name,
                    json.dumps({"checkpoint": checkpoint_name}),
                    run.trace_context.trace_id,
                    run.trace_context.span_id,
                ),
            )
            self._insert_audit(
                cursor, run.workspace_id, run.job_id, run.workflow_run_id, run.trace_context,
                "job.checkpointed", "allowed", {"checkpoint": checkpoint_name}
            )
            if checkpoint_name == "after_external_effect":
                cursor.execute(
                    "UPDATE jobs SET state = 'succeeded', finished_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (run.job_id,),
                )

    def _plan_effect(self, run: CanonicalRun, idempotency_key: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO external_effects (
                    workspace_id, job_id, effect_type, effect_classification, idempotency_key,
                    status, request_fingerprint, reconciliation_state
                ) VALUES (%s, %s, 'mock.write', 'write', %s, 'planned', %s, %s::jsonb)
                ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
                """,
                (run.workspace_id, run.job_id, idempotency_key, idempotency_key, json.dumps({"state": "planned"})),
            )

    def _complete_effect(
        self, run: CanonicalRun, idempotency_key: str, external_id: str, reconciled: bool
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE external_effects
                SET status = 'completed', provider_name = 'mock', provider_reference = %s,
                    reconciliation_state = %s::jsonb, effect_result = %s::jsonb,
                    completed_at = CURRENT_TIMESTAMP
                WHERE workspace_id = %s AND idempotency_key = %s
                """,
                (
                    external_id,
                    json.dumps({"reconciled": reconciled}),
                    json.dumps({"external_id": external_id}),
                    run.workspace_id,
                    idempotency_key,
                ),
            )
            self._insert_audit(
                cursor, run.workspace_id, run.job_id, run.workflow_run_id, run.trace_context,
                "external_effect.reconciled" if reconciled else "external_effect.completed",
                "allowed", {"external_id": external_id, "reconciled": reconciled}
            )
            cursor.execute(
                """
                INSERT INTO provenance_records (
                    workspace_id, job_id, origin_type, source_uri, source_hash,
                    verification_status, c2pa_manifest, lineage, trace_id, span_id
                ) VALUES (
                    %s, %s, 'mock_provider', %s, %s,
                    'verified', '{}'::jsonb, %s::jsonb, %s, %s
                )
                """,
                (
                    run.workspace_id,
                    run.job_id,
                    f"mock://external-effects/{external_id}",
                    external_id,
                    json.dumps({"idempotency_key": idempotency_key}),
                    run.trace_context.trace_id,
                    run.trace_context.span_id,
                ),
            )

    def _counts(self, run: CanonicalRun) -> CanonicalCounts:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM job_checkpoints WHERE job_id = %s),
                    (SELECT COUNT(*) FROM external_effects WHERE job_id = %s),
                    (SELECT COUNT(*) FROM audit_events WHERE job_id = %s),
                    (SELECT COUNT(*) FROM provenance_records WHERE job_id = %s)
                """,
                (run.job_id, run.job_id, run.job_id, run.job_id),
            )
            return CanonicalCounts(*cursor.fetchone())

    @staticmethod
    def _insert_audit(
        cursor: psycopg.Cursor,
        workspace_id: UUID,
        job_id: UUID,
        run_id: str,
        trace_context: TraceContext,
        action: str,
        outcome: str,
        details: dict[str, object],
    ) -> None:
        cursor.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM audit_events WHERE run_id = %s",
            (run_id,),
        )
        sequence_no = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO audit_events (
                workspace_id, job_id, run_id, sequence_no, actor_kind, action,
                resource_type, resource_id, outcome, trace_id, span_id, details
            ) VALUES (
                %s, %s, %s, %s, 'system', %s,
                'job', %s, %s, %s, %s, %s::jsonb
            )
            """,
            (
                workspace_id,
                job_id,
                run_id,
                sequence_no,
                action,
                str(job_id),
                outcome,
                trace_context.trace_id,
                trace_context.span_id,
                json.dumps(details),
            ),
        )

