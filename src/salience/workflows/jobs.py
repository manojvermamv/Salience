import asyncio
import os
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from salience.fixtures.mock_effect_memory import MockEffectProvider
from salience.governance.policy import (
    AuthorizationRequest,
    EffectClass,
    PolicyEngine,
    PolicyVersion,
)
from salience.governance.scopes import ScopeGrant
from salience.observability.tracing import TraceContext
from salience.workflows.effects import EffectProvider, EffectRequest, ExternalEffectService
from salience.workflows.persistence import CanonicalCounts, CanonicalJobStore, CanonicalRun


TASK_QUEUE_PREFIX = "salience-phase-one-dummy"
MAX_EFFECT_ATTEMPTS = 3


@dataclass(frozen=True)
class DummyWorkflowRequest:
    idempotency_key: str
    mode: str = "success"
    dry_run: bool = False
    effect_delay_seconds: float = 0.0
    crash_after_remote_acceptance: bool = False


@workflow.defn
class DurableDummyWorkflow:
    def __init__(self) -> None:
        self._cancellation_requested = False

    @workflow.signal
    def request_cancellation(self) -> None:
        self._cancellation_requested = True

    @workflow.run
    async def run(self, request: DummyWorkflowRequest) -> str:
        await workflow.execute_activity(
            "salience.checkpoint",
            "before_external_effect",
            start_to_close_timeout=timedelta(seconds=5),
        )
        if request.mode == "cancel":
            await workflow.wait_condition(lambda: self._cancellation_requested)
            await workflow.execute_activity(
                "salience.terminal",
                "cancelled",
                start_to_close_timeout=timedelta(seconds=5),
            )
            return "cancelled"

        timeout = (
            timedelta(milliseconds=100)
            if request.mode == "timeout"
            else timedelta(seconds=5)
        )
        try:
            receipt = await workflow.execute_activity(
                "salience.external_effect",
                request,
                start_to_close_timeout=timeout,
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(milliseconds=100),
                    maximum_interval=timedelta(seconds=1),
                    maximum_attempts=MAX_EFFECT_ATTEMPTS,
                ),
            )
        except Exception:
            status = "timed_out" if request.mode == "timeout" else "dead_lettered"
            await workflow.execute_activity(
                "salience.dead_letter",
                {"status": status},
                start_to_close_timeout=timedelta(seconds=5),
            )
            return status

        await workflow.execute_activity(
            "salience.checkpoint",
            "after_external_effect",
            start_to_close_timeout=timedelta(seconds=5),
        )
        return "succeeded"


@dataclass
class WorkflowScenarioState:
    provider: EffectProvider = field(default_factory=MockEffectProvider)
    checkpoints: list[str] = field(default_factory=list)
    effect_accepted: asyncio.Event = field(default_factory=asyncio.Event)
    checkpoint_recorded: asyncio.Event = field(default_factory=asyncio.Event)
    crash_after_acceptance: bool = False
    crashed: bool = False
    reconciled: bool = False
    effect_attempts: int = 0
    external_reference: str = ""
    store: CanonicalJobStore | None = None
    canonical_run: CanonicalRun | None = None


class DummyActivities:
    def __init__(self, state: WorkflowScenarioState) -> None:
        self._state = state

    async def _run(self) -> CanonicalRun:
        if self._state.canonical_run is not None:
            return self._state.canonical_run
        return await self._store.run_for_workflow(activity.info().workflow_id)

    @property
    def _store(self) -> CanonicalJobStore:
        if self._state.store is None:
            raise RuntimeError("canonical store was not configured")
        return self._state.store

    @activity.defn(name="salience.checkpoint")
    async def checkpoint(self, checkpoint_name: str) -> None:
        self._state.checkpoints.append(checkpoint_name)
        await self._store.checkpoint(await self._run(), checkpoint_name)
        self._state.checkpoint_recorded.set()

    @activity.defn(name="salience.external_effect")
    async def external_effect(self, request: DummyWorkflowRequest) -> str:
        run = await self._run()
        await self._store.plan_effect(run, request.idempotency_key)
        if not request.dry_run:
            self._state.effect_attempts += 1
        if request.mode == "retry_exhausted":
            raise ApplicationError("fixture external effect failed", type="fixture_failure")
        if request.effect_delay_seconds:
            await asyncio.sleep(request.effect_delay_seconds)

        receipt = await ExternalEffectService(self._state.provider).execute_or_reconcile(
            EffectRequest(
                idempotency_key=request.idempotency_key,
                effect_type="mock.write",
                dry_run=request.dry_run,
            )
        )
        if receipt.reconciled:
            self._state.reconciled = True
        self._state.external_reference = receipt.external_id
        if request.crash_after_remote_acceptance and not receipt.reconciled:
            os._exit(137)
        if self._state.crash_after_acceptance and not self._state.crashed:
            self._state.crashed = True
            self._state.effect_accepted.set()
            await asyncio.Event().wait()
        await self._store.complete_effect(
            run,
            idempotency_key=request.idempotency_key,
            external_id=receipt.external_id,
            reconciled=receipt.reconciled,
        )
        return receipt.external_id

    @activity.defn(name="salience.terminal")
    async def terminal(self, status: str) -> None:
        await self._store.terminal(await self._run(), status)

    @activity.defn(name="salience.dead_letter")
    async def dead_letter(self, payload: dict[str, str]) -> None:
        status = payload["status"]
        await self._store.dead_letter(
            await self._run(),
            attempt=self._state.effect_attempts,
            error_type="activity_timeout" if status == "timed_out" else "fixture_failure",
            error_message=status,
        )
        if status == "timed_out":
            await self._store.terminal(await self._run(), status)


@dataclass(frozen=True)
class RestartScenarioResult:
    status: str
    checkpoint_count: int
    provider_effect_calls: int
    reconciled: bool
    canonical_checkpoint_count: int
    canonical_effect_count: int
    audit_count: int
    provenance_count: int


@dataclass(frozen=True)
class TerminalScenarioResult:
    status: str
    effect_attempts: int
    checkpoint_count: int
    dead_letter_count: int
    external_reference: str = ""


@dataclass(frozen=True)
class TerminalScenariosResult:
    retry_exhausted: TerminalScenarioResult
    timed_out: TerminalScenarioResult
    cancelled: TerminalScenarioResult
    policy_denied: TerminalScenarioResult
    budget_denied: TerminalScenarioResult
    dry_run: TerminalScenarioResult


def build_worker(
    client: Client,
    *,
    task_queue: str,
    state: WorkflowScenarioState,
) -> Worker:
    activities = DummyActivities(state)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[DurableDummyWorkflow],
        activities=[
            activities.checkpoint,
            activities.external_effect,
            activities.terminal,
            activities.dead_letter,
        ],
    )


async def _shutdown_worker(worker: Worker, worker_task: asyncio.Task[None]) -> None:
    if not worker.is_shutdown:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
    if not worker_task.done():
        await asyncio.wait_for(worker_task, timeout=10)


async def _run_workflow_scenario(
    *,
    temporal_target: str,
    database_url: str,
    request: DummyWorkflowRequest,
) -> TerminalScenarioResult:
    client = await Client.connect(temporal_target)
    task_queue = f"{TASK_QUEUE_PREFIX}-{uuid4()}"
    workflow_id = f"terminal-contract-{uuid4()}"
    store = CanonicalJobStore(database_url)
    state = WorkflowScenarioState(store=store)
    state.canonical_run = await store.create_run(
        workflow_run_id=workflow_id,
        task_queue=task_queue,
        idempotency_key=request.idempotency_key,
        dry_run=request.dry_run,
    )
    worker = build_worker(client, task_queue=task_queue, state=state)
    worker_task = asyncio.create_task(worker.run())
    status = ""
    try:
        handle = await client.start_workflow(
            DurableDummyWorkflow.run,
            request,
            id=workflow_id,
            task_queue=task_queue,
        )
        if request.mode == "cancel":
            await asyncio.wait_for(state.checkpoint_recorded.wait(), timeout=10)
            await handle.signal("request_cancellation")
        status = await asyncio.wait_for(handle.result(), timeout=30)
    finally:
        await _shutdown_worker(worker, worker_task)

    counts = await store.counts(state.canonical_run)
    return TerminalScenarioResult(
        status=status,
        effect_attempts=state.effect_attempts,
        checkpoint_count=counts.checkpoint_count,
        dead_letter_count=counts.dead_letter_count,
        external_reference=state.external_reference,
    )


async def _run_preflight_denial(
    *,
    database_url: str,
    reason: str,
) -> TerminalScenarioResult:
    subject_id = uuid4()
    policy_id = uuid4()
    policy = PolicyVersion(
        id=policy_id,
        status="active",
        allowed_effects=frozenset({EffectClass.WRITE}),
        approval_required_effects=(
            frozenset({EffectClass.WRITE}) if reason == "approval" else frozenset()
        ),
        expires_at=None,
    )
    known_scopes = {"mock.write"}
    grant = ScopeGrant(subject_id=subject_id, scopes=frozenset(known_scopes))
    engine = PolicyEngine(
        policies={policy_id: policy},
        known_scopes=known_scopes,
        grants={subject_id: grant},
    )
    decision = engine.authorize(
        AuthorizationRequest(
            subject_id=subject_id,
            required_scopes=frozenset(known_scopes),
            effect_class=EffectClass.WRITE,
            policy_version_id=policy_id,
            dry_run=False,
            trace_context=TraceContext.new_root(),
        ),
        estimated_micros=11 if reason == "budget" else 1,
        available_micros=10,
        approved=False,
    )
    if decision.allowed:
        raise AssertionError("fixture preflight must be denied")

    store = CanonicalJobStore(database_url)
    run = await store.create_run(
        workflow_run_id=f"preflight-denied-{uuid4()}",
        task_queue=TASK_QUEUE_PREFIX,
        idempotency_key=f"preflight-{reason}-{uuid4()}",
    )
    await store.terminal(run, "denied")
    counts = await store.counts(run)
    return TerminalScenarioResult(
        status="denied",
        effect_attempts=0,
        checkpoint_count=counts.checkpoint_count,
        dead_letter_count=counts.dead_letter_count,
    )


async def run_terminal_state_scenarios(
    *, temporal_target: str, database_url: str
) -> TerminalScenariosResult:
    retry_exhausted = await _run_workflow_scenario(
        temporal_target=temporal_target,
        database_url=database_url,
        request=DummyWorkflowRequest(
            idempotency_key=f"retry-{uuid4()}", mode="retry_exhausted"
        ),
    )
    timed_out = await _run_workflow_scenario(
        temporal_target=temporal_target,
        database_url=database_url,
        request=DummyWorkflowRequest(
            idempotency_key=f"timeout-{uuid4()}",
            mode="timeout",
            effect_delay_seconds=1,
        ),
    )
    cancelled = await _run_workflow_scenario(
        temporal_target=temporal_target,
        database_url=database_url,
        request=DummyWorkflowRequest(idempotency_key=f"cancel-{uuid4()}", mode="cancel"),
    )
    policy_denied = await _run_preflight_denial(
        database_url=database_url, reason="approval"
    )
    budget_denied = await _run_preflight_denial(
        database_url=database_url, reason="budget"
    )
    dry_run = await _run_workflow_scenario(
        temporal_target=temporal_target,
        database_url=database_url,
        request=DummyWorkflowRequest(idempotency_key=f"dry-run-{uuid4()}", dry_run=True),
    )
    return TerminalScenariosResult(
        retry_exhausted=retry_exhausted,
        timed_out=timed_out,
        cancelled=cancelled,
        policy_denied=policy_denied,
        budget_denied=budget_denied,
        dry_run=dry_run,
    )


async def run_restart_reconciliation_scenario(
    *,
    temporal_target: str,
    database_url: str,
) -> RestartScenarioResult:
    client = await Client.connect(temporal_target)
    task_queue = f"{TASK_QUEUE_PREFIX}-{uuid4()}"
    workflow_id = f"restart-contract-{uuid4()}"
    idempotency_key = f"external-effect-{uuid4()}"
    store = CanonicalJobStore(database_url)
    state = WorkflowScenarioState(store=store, crash_after_acceptance=True)
    state.canonical_run = await store.create_run(
        workflow_run_id=workflow_id,
        task_queue=task_queue,
        idempotency_key=idempotency_key,
    )

    first_worker = build_worker(client, task_queue=task_queue, state=state)
    first_worker_task = asyncio.create_task(first_worker.run())
    try:
        handle = await client.start_workflow(
            DurableDummyWorkflow.run,
            DummyWorkflowRequest(idempotency_key=idempotency_key),
            id=workflow_id,
            task_queue=task_queue,
        )
        await asyncio.wait_for(state.effect_accepted.wait(), timeout=10)
        await _shutdown_worker(first_worker, first_worker_task)

        replacement_worker = build_worker(client, task_queue=task_queue, state=state)
        replacement_worker_task = asyncio.create_task(replacement_worker.run())
        try:
            await asyncio.wait_for(handle.result(), timeout=20)
        finally:
            await _shutdown_worker(replacement_worker, replacement_worker_task)
    finally:
        await _shutdown_worker(first_worker, first_worker_task)

    counts: CanonicalCounts = await store.counts(state.canonical_run)
    return RestartScenarioResult(
        status="succeeded",
        checkpoint_count=len(state.checkpoints),
        provider_effect_calls=state.provider.call_count,
        reconciled=state.reconciled,
        canonical_checkpoint_count=counts.checkpoint_count,
        canonical_effect_count=counts.effect_count,
        audit_count=counts.audit_count,
        provenance_count=counts.provenance_count,
    )
