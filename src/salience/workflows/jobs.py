import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from salience.fixtures.mock_effect_provider import MockEffectProvider
from salience.workflows.persistence import CanonicalJobStore, CanonicalRun


TASK_QUEUE_PREFIX = "salience-phase-one-dummy"


@workflow.defn
class DurableDummyWorkflow:
    @workflow.run
    async def run(self, idempotency_key: str) -> str:
        await workflow.execute_activity(
            "salience.checkpoint",
            "before_external_effect",
            start_to_close_timeout=timedelta(seconds=5),
        )
        receipt = await workflow.execute_activity(
            "salience.external_effect",
            idempotency_key,
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=100),
                maximum_interval=timedelta(seconds=1),
                maximum_attempts=3,
            ),
        )
        await workflow.execute_activity(
            "salience.checkpoint",
            "after_external_effect",
            start_to_close_timeout=timedelta(seconds=5),
        )
        return receipt


@dataclass
class RestartScenarioState:
    provider: MockEffectProvider = field(default_factory=MockEffectProvider)
    checkpoints: list[str] = field(default_factory=list)
    effect_accepted: asyncio.Event = field(default_factory=asyncio.Event)
    crash_once: bool = True
    reconciled: bool = False
    store: CanonicalJobStore | None = None
    canonical_run: CanonicalRun | None = None


class DummyActivities:
    def __init__(self, state: RestartScenarioState) -> None:
        self._state = state

    @activity.defn(name="salience.checkpoint")
    async def checkpoint(self, checkpoint_name: str) -> None:
        self._state.checkpoints.append(checkpoint_name)
        assert self._state.store is not None
        assert self._state.canonical_run is not None
        await self._state.store.checkpoint(self._state.canonical_run, checkpoint_name)

    @activity.defn(name="salience.external_effect")
    async def external_effect(self, idempotency_key: str) -> str:
        assert self._state.store is not None
        assert self._state.canonical_run is not None
        await self._state.store.plan_effect(self._state.canonical_run, idempotency_key)
        receipt = await self._state.provider.execute_or_reconcile(idempotency_key)
        if receipt.reconciled:
            self._state.reconciled = True
        if self._state.crash_once:
            self._state.crash_once = False
            self._state.effect_accepted.set()
            await asyncio.Event().wait()
        await self._state.store.complete_effect(
            self._state.canonical_run,
            idempotency_key=idempotency_key,
            external_id=receipt.external_id,
            reconciled=receipt.reconciled,
        )
        return receipt.external_id


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


def build_worker(
    client: Client,
    *,
    task_queue: str,
    state: RestartScenarioState,
) -> Worker:
    activities = DummyActivities(state)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[DurableDummyWorkflow],
        activities=[activities.checkpoint, activities.external_effect],
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
    state = RestartScenarioState(store=store)
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
            idempotency_key,
            id=workflow_id,
            task_queue=task_queue,
        )
        await asyncio.wait_for(state.effect_accepted.wait(), timeout=10)
        await asyncio.wait_for(first_worker.shutdown(), timeout=10)
        await asyncio.wait_for(first_worker_task, timeout=10)

        replacement_worker = build_worker(client, task_queue=task_queue, state=state)
        replacement_worker_task = asyncio.create_task(replacement_worker.run())
        try:
            await asyncio.wait_for(handle.result(), timeout=20)
        finally:
            await asyncio.wait_for(replacement_worker.shutdown(), timeout=10)
            await asyncio.wait_for(replacement_worker_task, timeout=10)
    finally:
        if not first_worker.is_shutdown:
            await first_worker.shutdown()
        if not first_worker_task.done():
            await first_worker_task

    counts = await store.counts(state.canonical_run)
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
