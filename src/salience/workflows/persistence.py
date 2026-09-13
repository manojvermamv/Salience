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
    dead_letter_count: int


@dataclass(frozen=True)
class CanonicalEffect:
    status: str
    external_id: str | None


@dataclass(frozen=True)
class CanonicalJobSnapshot:
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    audit_events: list[dict[str, object]]
    provenance_records: list[dict[str, object]]
    cost_entries: list[dict[str, object]]
    output_payload: dict[str, object]


@dataclass(frozen=True)
class CanonicalWorkspace:
    workspace_id: str
    slug: str
    display_name: str


@dataclass(frozen=True)
class CanonicalContentProgram:
    content_program_id: str
    workspace_id: str
    slug: str
    name: str
    niche: str


@dataclass(frozen=True)
class CanonicalSchedule:
    schedule_id: str
    workspace_id: str
    content_program_id: str
    name: str
    job_type: str
    schedule_expression: str


class CanonicalJobStore:
    """Synchronous Psycopg operations dispatched off Temporal's activity event loop."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def create_run(
        self,
        *,
        workflow_run_id: str,
        task_queue: str,
        idempotency_key: str,
        dry_run: bool = False,
    ) -> CanonicalRun:
        return await asyncio.to_thread(
            self._create_run, workflow_run_id, task_queue, idempotency_key, dry_run
        )

    async def create_intelligence_run(
        self,
        *,
        workflow_run_id: str,
        task_queue: str,
        workspace_id: str,
        content_program_id: str,
        niche: str,
        idempotency_key: str,
        dry_run: bool,
    ) -> CanonicalRun:
        return await asyncio.to_thread(
            self._create_intelligence_run,
            workflow_run_id,
            task_queue,
            workspace_id,
            content_program_id,
            niche,
            idempotency_key,
            dry_run,
        )

    async def create_creative_run(
        self,
        *,
        workflow_run_id: str,
        task_queue: str,
        workspace_id: str,
        content_program_id: str,
        brief_id: str,
        idempotency_key: str,
        dry_run: bool,
    ) -> CanonicalRun:
        return await asyncio.to_thread(
            self._create_creative_run,
            workflow_run_id,
            task_queue,
            workspace_id,
            content_program_id,
            brief_id,
            idempotency_key,
            dry_run,
        )

    async def checkpoint(self, run: CanonicalRun, checkpoint_name: str) -> None:
        await asyncio.to_thread(self._checkpoint, run, checkpoint_name)

    async def plan_effect(self, run: CanonicalRun, idempotency_key: str) -> None:
        await asyncio.to_thread(self._plan_effect, run, idempotency_key)

    async def begin_effect_submission(self, run: CanonicalRun, idempotency_key: str) -> bool:
        return await asyncio.to_thread(self._begin_effect_submission, run, idempotency_key)

    async def effect(self, run: CanonicalRun, idempotency_key: str) -> CanonicalEffect | None:
        return await asyncio.to_thread(self._effect, run, idempotency_key)

    async def complete_effect(
        self, run: CanonicalRun, *, idempotency_key: str, external_id: str, reconciled: bool
    ) -> None:
        await asyncio.to_thread(
            self._complete_effect, run, idempotency_key, external_id, reconciled
        )

    async def terminal(self, run: CanonicalRun, status: str) -> None:
        await asyncio.to_thread(self._terminal, run, status)

    async def complete_with_output(
        self, run: CanonicalRun, *, status: str, output: dict[str, object]
    ) -> None:
        await asyncio.to_thread(self._complete_with_output, run, status, output)

    async def dead_letter(
        self,
        run: CanonicalRun,
        *,
        attempt: int,
        error_type: str,
        error_message: str,
    ) -> None:
        await asyncio.to_thread(
            self._dead_letter, run, attempt, error_type, error_message
        )

    async def counts(self, run: CanonicalRun) -> CanonicalCounts:
        return await asyncio.to_thread(self._counts, run)

    async def run_for_workflow(self, workflow_run_id: str) -> CanonicalRun:
        return await asyncio.to_thread(self._run_for_workflow, workflow_run_id)

    async def effect_was_reconciled(
        self, run: CanonicalRun, idempotency_key: str
    ) -> bool:
        return await asyncio.to_thread(
            self._effect_was_reconciled, run, idempotency_key
        )

    async def job_snapshot(self, job_id: str) -> CanonicalJobSnapshot | None:
        return await asyncio.to_thread(self._job_snapshot, job_id)

    async def job_by_idempotency_key(
        self, idempotency_key: str
    ) -> CanonicalJobSnapshot | None:
        return await asyncio.to_thread(self._job_by_idempotency_key, idempotency_key)

    async def create_workspace(
        self, *, slug: str, display_name: str
    ) -> CanonicalWorkspace:
        return await asyncio.to_thread(self._create_workspace, slug, display_name)

    async def create_content_program(
        self, *, workspace_id: str, slug: str, name: str, niche: str
    ) -> CanonicalContentProgram:
        return await asyncio.to_thread(
            self._create_content_program, workspace_id, slug, name, niche
        )

    async def create_schedule(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        name: str,
        schedule_expression: str,
        job_type: str,
        payload: dict[str, object],
    ) -> CanonicalSchedule:
        return await asyncio.to_thread(
            self._create_schedule,
            workspace_id,
            content_program_id,
            name,
            schedule_expression,
            job_type,
            payload,
        )

    def _connect(self) -> psycopg.Connection:
        dsn = self._database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        return psycopg.connect(dsn)

    def _create_run(
        self,
        workflow_run_id: str,
        task_queue: str,
        idempotency_key: str,
        dry_run: bool,
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
                    %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s
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
                    dry_run,
                ),
            )
            self._insert_audit(
                cursor, workspace_id, job_id, workflow_run_id, trace_context, "job.started",
                "allowed", {"task_queue": task_queue}
            )
        return CanonicalRun(
            workspace_id, content_program_id, job_id, workflow_run_id, trace_context
        )

    def _create_intelligence_run(
        self,
        workflow_run_id: str,
        task_queue: str,
        workspace_id: str,
        content_program_id: str,
        niche: str,
        idempotency_key: str,
        dry_run: bool,
    ) -> CanonicalRun:
        trace_context = TraceContext.new_root()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM content_programs
                WHERE id = %s AND workspace_id = %s
                """,
                (content_program_id, workspace_id),
            )
            if cursor.fetchone() is None:
                raise KeyError("content program does not belong to workspace")
            cursor.execute(
                """
                INSERT INTO jobs (
                    workspace_id, content_program_id, job_type, state, workflow_run_id,
                    task_queue, idempotency_key, input_payload, retry_policy, trace_id, span_id,
                    dry_run, started_at
                ) VALUES (
                    %s, %s, 'intelligence_research', 'running', %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, CURRENT_TIMESTAMP
                )
                ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
                RETURNING id, trace_id, span_id
                """,
                (
                    workspace_id,
                    content_program_id,
                    workflow_run_id,
                    task_queue,
                    idempotency_key,
                    json.dumps({"niche": niche, "contract_version": "IntelligenceRunRequest@v1"}),
                    json.dumps({"maximum_attempts": 3, "initial_backoff_ms": 100}),
                    trace_context.trace_id,
                    trace_context.span_id,
                    dry_run,
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                cursor.execute(
                    """
                    SELECT id, workflow_run_id, trace_id, span_id
                    FROM jobs
                    WHERE workspace_id = %s AND idempotency_key = %s
                    """,
                    (workspace_id, idempotency_key),
                )
                job_id, existing_workflow_id, trace_id, span_id = cursor.fetchone()
                return CanonicalRun(
                    workspace_id=UUID(workspace_id),
                    content_program_id=UUID(content_program_id),
                    job_id=job_id,
                    workflow_run_id=existing_workflow_id,
                    trace_context=TraceContext(trace_id=trace_id, span_id=span_id),
                )
            job_id, trace_id, span_id = inserted
            run = CanonicalRun(
                workspace_id=UUID(workspace_id),
                content_program_id=UUID(content_program_id),
                job_id=job_id,
                workflow_run_id=workflow_run_id,
                trace_context=TraceContext(trace_id=trace_id, span_id=span_id),
            )
            self._insert_audit(
                cursor,
                run.workspace_id,
                run.job_id,
                run.workflow_run_id,
                run.trace_context,
                "intelligence_run.started",
                "allowed",
                {"task_queue": task_queue, "dry_run": dry_run},
            )
        return run

    def _create_creative_run(
        self,
        workflow_run_id: str,
        task_queue: str,
        workspace_id: str,
        content_program_id: str,
        brief_id: str,
        idempotency_key: str,
        dry_run: bool,
    ) -> CanonicalRun:
        trace_context = TraceContext.new_root()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM content_brief_versions
                WHERE id = %s AND workspace_id = %s AND content_program_id = %s
                """,
                (brief_id, workspace_id, content_program_id),
            )
            if cursor.fetchone() is None:
                raise KeyError("content brief is outside the requested workspace/program")
            cursor.execute(
                """
                INSERT INTO jobs (
                    workspace_id, content_program_id, job_type, state, workflow_run_id,
                    task_queue, idempotency_key, input_payload, retry_policy, trace_id, span_id,
                    dry_run, started_at
                ) VALUES (
                    %s, %s, 'creative_production', 'running', %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, CURRENT_TIMESTAMP
                )
                ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
                RETURNING id, trace_id, span_id
                """,
                (
                    workspace_id,
                    content_program_id,
                    workflow_run_id,
                    task_queue,
                    idempotency_key,
                    json.dumps(
                        {
                            "brief_id": brief_id,
                            "contract_version": "CreativeProductionRequest@v1",
                        }
                    ),
                    json.dumps({"maximum_attempts": 3, "initial_backoff_ms": 100}),
                    trace_context.trace_id,
                    trace_context.span_id,
                    dry_run,
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                cursor.execute(
                    """
                    SELECT id, workflow_run_id, trace_id, span_id
                    FROM jobs
                    WHERE workspace_id = %s AND idempotency_key = %s
                    """,
                    (workspace_id, idempotency_key),
                )
                job_id, existing_workflow_id, trace_id, span_id = cursor.fetchone()
                return CanonicalRun(
                    workspace_id=UUID(workspace_id),
                    content_program_id=UUID(content_program_id),
                    job_id=job_id,
                    workflow_run_id=existing_workflow_id,
                    trace_context=TraceContext(trace_id=trace_id, span_id=span_id),
                )
            job_id, trace_id, span_id = inserted
            run = CanonicalRun(
                workspace_id=UUID(workspace_id),
                content_program_id=UUID(content_program_id),
                job_id=job_id,
                workflow_run_id=workflow_run_id,
                trace_context=TraceContext(trace_id=trace_id, span_id=span_id),
            )
            self._insert_audit(
                cursor,
                run.workspace_id,
                run.job_id,
                run.workflow_run_id,
                run.trace_context,
                "creative_run.started",
                "allowed",
                {"task_queue": task_queue, "dry_run": dry_run, "brief_id": brief_id},
            )
        return run

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
                self._update_terminal(cursor, run, "succeeded")

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

    def _begin_effect_submission(self, run: CanonicalRun, idempotency_key: str) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE external_effects
                SET status = 'submitting', attempted_at = CURRENT_TIMESTAMP,
                    reconciliation_state = %s::jsonb, updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = %s AND idempotency_key = %s AND status = 'planned'
                RETURNING id
                """,
                (json.dumps({"state": "submitting"}), run.workspace_id, idempotency_key),
            )
            return cursor.fetchone() is not None

    def _effect(self, run: CanonicalRun, idempotency_key: str) -> CanonicalEffect | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, provider_reference
                FROM external_effects
                WHERE workspace_id = %s AND idempotency_key = %s
                """,
                (run.workspace_id, idempotency_key),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return CanonicalEffect(status=row[0], external_id=row[1])
            self._insert_audit(
                cursor,
                run.workspace_id,
                run.job_id,
                run.workflow_run_id,
                run.trace_context,
                "external_effect.reconciled" if reconciled else "external_effect.completed",
                "allowed",
                {"external_id": external_id, "reconciled": reconciled},
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

    def _terminal(self, run: CanonicalRun, status: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            self._update_terminal(cursor, run, status)
            self._insert_audit(
                cursor,
                run.workspace_id,
                run.job_id,
                run.workflow_run_id,
                run.trace_context,
                f"job.{status}",
                "denied" if status == "denied" else "completed",
                {"status": status},
            )

    def _complete_with_output(
        self, run: CanonicalRun, status: str, output: dict[str, object]
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET state = %s, output_payload = %s::jsonb, finished_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (status, json.dumps(output), run.job_id),
            )
            self._insert_audit(
                cursor,
                run.workspace_id,
                run.job_id,
                run.workflow_run_id,
                run.trace_context,
                f"intelligence_run.{status}",
                "completed" if status == "succeeded" else "failed",
                output,
            )

    def _dead_letter(
        self,
        run: CanonicalRun,
        attempt: int,
        error_type: str,
        error_message: str,
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO job_dead_letters (
                    job_id, attempt, error_type, error_message, retryable, failed_at
                ) VALUES (%s, %s, %s, %s, FALSE, CURRENT_TIMESTAMP)
                ON CONFLICT (job_id, attempt) DO NOTHING
                """,
                (run.job_id, attempt, error_type, error_message),
            )
            cursor.execute(
                "UPDATE jobs SET attempt = %s WHERE id = %s",
                (attempt, run.job_id),
            )
            self._update_terminal(cursor, run, "dead_lettered")
            self._insert_audit(
                cursor,
                run.workspace_id,
                run.job_id,
                run.workflow_run_id,
                run.trace_context,
                "job.dead_lettered",
                "failed",
                {"attempt": attempt, "error_type": error_type},
            )

    @staticmethod
    def _update_terminal(cursor: psycopg.Cursor, run: CanonicalRun, status: str) -> None:
        cursor.execute(
            "UPDATE jobs SET state = %s, finished_at = CURRENT_TIMESTAMP WHERE id = %s",
            (status, run.job_id),
        )

    def _counts(self, run: CanonicalRun) -> CanonicalCounts:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM job_checkpoints WHERE job_id = %s),
                    (SELECT COUNT(*) FROM external_effects WHERE job_id = %s),
                    (SELECT COUNT(*) FROM audit_events WHERE job_id = %s),
                    (SELECT COUNT(*) FROM provenance_records WHERE job_id = %s),
                    (SELECT COUNT(*) FROM job_dead_letters WHERE job_id = %s)
                """,
                (run.job_id, run.job_id, run.job_id, run.job_id, run.job_id),
            )
            return CanonicalCounts(*cursor.fetchone())

    def _run_for_workflow(self, workflow_run_id: str) -> CanonicalRun:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT workspace_id, content_program_id, id, workflow_run_id, trace_id, span_id
                FROM jobs
                WHERE workflow_run_id = %s
                """,
                (workflow_run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"canonical job not found for workflow {workflow_run_id}")
            workspace_id, content_program_id, job_id, run_id, trace_id, span_id = row
            return CanonicalRun(
                workspace_id=workspace_id,
                content_program_id=content_program_id,
                job_id=job_id,
                workflow_run_id=run_id,
                trace_context=TraceContext(trace_id=trace_id, span_id=span_id),
            )

    def _effect_was_reconciled(self, run: CanonicalRun, idempotency_key: str) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT reconciliation_state ->> 'reconciled'
                FROM external_effects
                WHERE workspace_id = %s AND idempotency_key = %s
                """,
                (run.workspace_id, idempotency_key),
            )
            row = cursor.fetchone()
            return row is not None and row[0] == "true"

    def _job_snapshot(self, job_id: str) -> CanonicalJobSnapshot | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT state, dry_run, trace_id, COALESCE(output_payload, '{}'::jsonb) FROM jobs WHERE id = %s",
                (job_id,),
            )
            job = cursor.fetchone()
            if job is None:
                return None
            state, dry_run, trace_id, output_payload = job
            cursor.execute(
                """
                SELECT action, outcome, details
                FROM audit_events WHERE job_id = %s ORDER BY sequence_no
                """,
                (job_id,),
            )
            audit_events = [
                {"action": action, "outcome": outcome, "details": details}
                for action, outcome, details in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT origin_type, source_uri, verification_status, lineage
                FROM provenance_records WHERE job_id = %s ORDER BY created_at
                """,
                (job_id,),
            )
            provenance_records = [
                {
                    "origin_type": origin_type,
                    "source_uri": source_uri,
                    "verification_status": verification_status,
                    "lineage": lineage,
                }
                for origin_type, source_uri, verification_status, lineage in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT estimated_amount, actual_amount, currency, usage
                FROM cost_ledger_entries WHERE job_id = %s ORDER BY recorded_at
                """,
                (job_id,),
            )
            cost_entries = [
                {
                    "estimated_amount": str(estimated_amount),
                    "actual_amount": str(actual_amount) if actual_amount is not None else None,
                    "currency": currency,
                    "usage": usage,
                }
                for estimated_amount, actual_amount, currency, usage in cursor.fetchall()
            ]
            return CanonicalJobSnapshot(
                job_id=job_id,
                state=state,
                dry_run=dry_run,
                trace_id=trace_id,
                audit_events=audit_events,
                provenance_records=provenance_records,
                cost_entries=cost_entries,
                output_payload=output_payload,
            )

    def _job_by_idempotency_key(
        self, idempotency_key: str
    ) -> CanonicalJobSnapshot | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text FROM jobs
                WHERE idempotency_key = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (idempotency_key,),
            )
            row = cursor.fetchone()
        return self._job_snapshot(row[0]) if row is not None else None

    def _create_workspace(self, slug: str, display_name: str) -> CanonicalWorkspace:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO workspaces (slug, display_name)
                VALUES (%s, %s)
                ON CONFLICT (slug) DO UPDATE SET display_name = EXCLUDED.display_name
                RETURNING id::text, slug, display_name
                """,
                (slug, display_name),
            )
            workspace_id, persisted_slug, persisted_display_name = cursor.fetchone()
        return CanonicalWorkspace(workspace_id, persisted_slug, persisted_display_name)

    def _create_content_program(
        self, workspace_id: str, slug: str, name: str, niche: str
    ) -> CanonicalContentProgram:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO content_programs (workspace_id, slug, name, niche)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (workspace_id, slug)
                DO UPDATE SET name = EXCLUDED.name, niche = EXCLUDED.niche
                RETURNING id::text, workspace_id::text, slug, name, niche
                """,
                (workspace_id, slug, name, niche),
            )
            program_id, persisted_workspace_id, persisted_slug, persisted_name, persisted_niche = (
                cursor.fetchone()
            )
        return CanonicalContentProgram(
            program_id,
            persisted_workspace_id,
            persisted_slug,
            persisted_name,
            persisted_niche,
        )

    def _create_schedule(
        self,
        workspace_id: str,
        content_program_id: str,
        name: str,
        schedule_expression: str,
        job_type: str,
        payload: dict[str, object],
    ) -> CanonicalSchedule:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO job_schedules (
                    workspace_id, content_program_id, name, schedule_expression, job_type, payload
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (workspace_id, name)
                DO UPDATE SET
                    content_program_id = EXCLUDED.content_program_id,
                    schedule_expression = EXCLUDED.schedule_expression,
                    job_type = EXCLUDED.job_type,
                    payload = EXCLUDED.payload,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id::text, workspace_id::text, content_program_id::text, name,
                          job_type, schedule_expression
                """,
                (
                    workspace_id,
                    content_program_id,
                    name,
                    schedule_expression,
                    job_type,
                    json.dumps(payload),
                ),
            )
            return CanonicalSchedule(*cursor.fetchone())

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
