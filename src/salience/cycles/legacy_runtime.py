"""Isolated legacy intelligence execution behind the original V4 permit."""

import asyncio
from datetime import datetime, timedelta
import os

from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from salience.agents.fixtures import fixture_agent_service
from salience.cycles.governance import CycleGovernance
from salience.cycles.legacy_dispatch import require_fixture
from salience.cycles.legacy_schedule_workflow import LegacyScheduledIngressWorkflow, LEGACY_SCHEDULE_INGRESS
from salience.cycles.native_schedules import schedule_row, schedule_metadata, require_native_binding
from salience.cycles.outbox import CycleOutbox
from salience.cycles.runtime import LocalCycleActivities, TemporalCycleTransport
from salience.cycles.workflow import LocalCycleWorkflow
from salience.cycles.schedule_cutover import FixtureSchedulePoller
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.intelligence import (
    IntelligenceActivities, IntelligenceLoopRequest, IntelligenceLoopWorkflow,
    IntelligenceWorkflowState, INTELLIGENCE_WORKFLOW_TYPE,
)
from salience.workflows.jobs import DurableDummyWorkflow, DummyWorkflowRequest, DummyActivities, WorkflowScenarioState
from salience.workflows.persistence import CanonicalJobStore
from salience.workflows.creative import CreativeActivities, CreativeWorkflowState, CreativeProductionRequest, CreativeProductionWorkflow, CREATIVE_WORKFLOW_TYPE
from salience.creative.repository import CreativeRepository
from salience.creative.providers import FixtureCreativeProvider
from typing import Any


def binding_for_operation(outbox, operation_id):
    with outbox._connect() as connection:
        row = connection.execute("SELECT * FROM v4_legacy_dispatches WHERE operation_id=%s AND workspace_id=%s", (operation_id,outbox.workspace_id)).fetchone()
        if not row:
            raise PermissionError("original legacy operation required")
        return row


def require_current_stage(outbox, binding):
    require_fixture()
    service = CycleGovernance(outbox.database_url,workspace_id=outbox.workspace_id,subject_id=binding["actor_id"])
    with service._command("cycles:permit") as connection:
        service._eligible_cycle(connection, binding["cycle_id"])
        if binding.get("stage") in {"dummy", "creative"}:
            from salience.cycles.authority import current_authority
            current_authority(connection, outbox.workspace_id, binding["actor_id"], "legacy:"+binding["stage"])
        workspace_stop, goal_stop = service._require_running(connection,binding["goal_id"])
        claim = connection.execute("""SELECT claim.*,permit.expires_at,permit.subject_id,
            permit.workspace_stop_revision,permit.goal_stop_revision FROM v4_permit_claims claim
            JOIN v4_dispatch_permits permit ON permit.id=claim.current_permit_id
            WHERE claim.cycle_id=%s""",(binding["cycle_id"],)).fetchone()
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        if not claim or claim["state"] != "claimed" or claim["operation_id"] != binding["operation_id"] or claim["subject_id"] != binding["actor_id"] or now >= claim["expires_at"] or (workspace_stop["revision"],goal_stop["revision"]) != (claim["workspace_stop_revision"],claim["goal_stop_revision"]):
            raise PermissionError("current original legacy permit required")


def legacy_execution(binding):
    if binding.get("stage", "intelligence") == "dummy":
        return DurableDummyWorkflow.run, "DurableDummyWorkflow", DummyWorkflowRequest(
            idempotency_key="v4-legacy:"+str(binding["operation_id"]), mode="success", dry_run=True), DurableDummyWorkflow.request_cancellation
    if binding.get("stage") == "creative":
        return CreativeProductionWorkflow.run, CREATIVE_WORKFLOW_TYPE, CreativeProductionRequest(
            workspace_id=str(binding["workspace_id"]), content_program_id=str(binding["content_program_id"]),
            brief_id=binding["payload"]["brief_id"], idempotency_key="v4-legacy:"+str(binding["operation_id"]),
            dry_run=True, target_profile_key=binding["payload"]["target_profile_key"],
            target_profile_version=binding["payload"]["target_profile_version"], max_variants=binding["payload"]["max_variants"]), CreativeProductionWorkflow.request_cancellation
    if binding.get("stage", "intelligence") != "intelligence":
        raise PermissionError("unsupported original legacy stage")
    return IntelligenceLoopWorkflow.run, INTELLIGENCE_WORKFLOW_TYPE, IntelligenceLoopRequest(
        workspace_id=str(binding["workspace_id"]), content_program_id=str(binding["content_program_id"]),
        niche=binding["payload"]["niche"], idempotency_key="v4-legacy:"+str(binding["operation_id"]), dry_run=True,
        selected_opportunity_id=binding["payload"].get("selected_opportunity_id")), IntelligenceLoopWorkflow.request_cancellation


class LegacyIntelligenceAdapter:
    # The worker constructs only deterministic fixture agents; no model/provider
    # gateway or injected production adapter is accepted by this boundary.
    effect_id = "fixture.noop"

    def __init__(self, client, outbox, *, task_queue):
        require_fixture()
        if outbox.delivery_lane != "legacy":
            raise ValueError("legacy delivery lane required")
        TemporalCycleTransport(client, task_queue=task_queue)
        self.client, self.outbox, self.task_queue = client, outbox, task_queue

    async def invoke(self, *, operation_id, idempotency_key, traceparent):
        if idempotency_key != operation_id:
            raise ValueError("original operation key required")
        binding = await asyncio.to_thread(binding_for_operation,self.outbox,operation_id)
        if binding["task_queue"] != self.task_queue:
            raise PermissionError("pinned legacy worker route required")
        await asyncio.to_thread(require_current_stage,self.outbox,binding)
        workflow_id = "salience-v4-legacy-operation:" + operation_id
        memo = {"cycle_id":str(binding["cycle_id"]),"operation_id":operation_id,"job_id":str(binding["job_id"])}
        workflow_run, workflow_type, payload, _ = legacy_execution(binding)
        try:
            await self.client.start_workflow(workflow_run,payload,id=workflow_id,
                task_queue=self.task_queue,id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=timedelta(minutes=5),memo=memo,rpc_timeout=timedelta(seconds=2))
        except WorkflowAlreadyStartedError:
            description = await self.client.get_workflow_handle(workflow_id).describe(rpc_timeout=timedelta(seconds=2))
            if description.workflow_type != workflow_type or description.task_queue != self.task_queue or await description.memo() != memo:
                raise PermissionError("legacy runtime identity conflict")
        # A start acknowledgment proves handoff, never business completion.
        with self.outbox._connect() as connection:
            connection.execute("UPDATE jobs SET state='running',started_at=COALESCE(started_at,clock_timestamp()) WHERE id=%s AND state='queued'",(binding["job_id"],))
        return {"accepted":True,"operation_id":operation_id,"idempotency_key":operation_id}


class LegacyCycleActivities(LocalCycleActivities):
    @activity.defn(name="salience.v4.fixture_consume")
    async def consume(self, message: dict) -> dict:
        result = await super().consume(message)
        if message["kind"] == "start" and result["state"] == "held":
            # A hard exit may commit the claim before the handoff receipt. An
            # activity retry never issues another start; retain unknown state.
            def unknown():
                with self.outbox._connect() as connection:
                    row = connection.execute("SELECT claim.state,claim.current_permit_id,binding.actor_id FROM v4_permit_claims claim JOIN v4_legacy_dispatches binding ON binding.cycle_id=claim.cycle_id WHERE claim.cycle_id=%s AND binding.workspace_id=%s",(message["cycle_id"],self.outbox.workspace_id)).fetchone()
                if row and row["state"] == "claimed":
                    CycleGovernance(self.outbox.database_url,workspace_id=self.outbox.workspace_id,subject_id=row["actor_id"]).mark_unknown(row["current_permit_id"])
            await asyncio.to_thread(unknown)
        if message["kind"] == "close":
            def read():
                with self.outbox._connect() as connection:
                    return connection.execute("SELECT * FROM v4_legacy_dispatches WHERE cycle_id=%s AND workspace_id=%s", (message["cycle_id"],self.outbox.workspace_id)).fetchone()
            binding = await asyncio.to_thread(read)
            with self.outbox._connect() as connection:
                claim = connection.execute("SELECT state FROM v4_permit_claims WHERE cycle_id=%s",(message["cycle_id"],)).fetchone()
                if not claim:
                    connection.execute("UPDATE jobs SET state='cancelled',finished_at=clock_timestamp() WHERE id=%s AND state='queued'",(binding["job_id"],))
                    return result
            # Closing has already committed and passed ordered consumption. A
            # repeated cancellation signal is harmless and never starts work.
            handle = self.adapter.client.get_workflow_handle("salience-v4-legacy-operation:" + str(binding["operation_id"]))
            description = await handle.describe(rpc_timeout=timedelta(seconds=2))
            expected = {"cycle_id":str(binding["cycle_id"]),"operation_id":str(binding["operation_id"]),"job_id":str(binding["job_id"])}
            _, workflow_type, _, cancellation_signal = legacy_execution(binding)
            if description.workflow_type != workflow_type or description.task_queue != binding["task_queue"] or await description.memo() != expected:
                raise PermissionError("original legacy cancellation target required")
            from temporalio.client import WorkflowExecutionStatus
            if binding.get("stage") == "dummy":
                handle = self.adapter.client.get_workflow_handle(description.id, run_id=description.run_id)
                # Success-mode dummy does not observe its custom signal. Only
                # terminal readback may project physical cancellation.
                if description.status == WorkflowExecutionStatus.RUNNING:
                    try:
                        await handle.cancel(rpc_timeout=timedelta(seconds=2))
                    except Exception:
                        description = await handle.describe(rpc_timeout=timedelta(seconds=2))
                        if description.status == WorkflowExecutionStatus.RUNNING:
                            raise
                    try:
                        async with asyncio.timeout(2):
                            while description.status == WorkflowExecutionStatus.RUNNING:
                                await asyncio.sleep(.05)
                                description = await handle.describe(rpc_timeout=timedelta(seconds=1))
                    except TimeoutError:
                        pass
                if description.status == WorkflowExecutionStatus.CANCELED:
                    with self.outbox._connect() as connection:
                        connection.execute("UPDATE jobs SET state='cancelled',finished_at=clock_timestamp() WHERE id=%s AND state IN ('queued','running')", (binding["job_id"],))
                return result
            if description.status != WorkflowExecutionStatus.RUNNING:
                return result
            handle = self.adapter.client.get_workflow_handle(description.id, run_id=description.run_id)
            try:
                await handle.signal(cancellation_signal,rpc_timeout=timedelta(seconds=2))
            except Exception:
                description = await handle.describe(rpc_timeout=timedelta(seconds=2))
                if description.status == WorkflowExecutionStatus.RUNNING:
                    raise
        return result


class GuardedIntelligenceActivities(IntelligenceActivities):
    def __init__(self,state,outbox):
        super().__init__(state)
        self.outbox = outbox

    async def _run(self):
        run = await super()._run()
        with self.outbox._connect() as connection:
            binding = connection.execute("SELECT * FROM v4_legacy_dispatches WHERE job_id=%s AND workspace_id=%s", (run.job_id,self.outbox.workspace_id)).fetchone()
        if not binding or binding.get("stage", "intelligence") != "intelligence":
            raise PermissionError("original canonical intelligence stage required")
        if activity.info().activity_type != "salience.intelligence.cancel":
            await asyncio.to_thread(require_current_stage,self.outbox,binding)
        return run

    @activity.defn(name="salience.intelligence.fetch")
    async def fetch(self, request: IntelligenceLoopRequest) -> dict[str, Any]:
        run = await self._run()
        binding = await asyncio.to_thread(binding_for_job, self.outbox, run.job_id)
        if request != legacy_execution(binding)[2]:
            raise PermissionError("original fixed no-send intelligence payload required")
        return await super().fetch(request)


class LegacyNoSendProvider:
    async def execute_or_reconcile(self, idempotency_key):
        raise PermissionError("legacy dummy never sends a provider effect")


class GuardedDummyActivities(DummyActivities):
    def __init__(self, state, outbox):
        super().__init__(state)
        self.outbox = outbox

    async def _run(self):
        run = await super()._run()
        with self.outbox._connect() as connection:
            binding = connection.execute("SELECT * FROM v4_legacy_dispatches WHERE job_id=%s AND workspace_id=%s", (run.job_id, self.outbox.workspace_id)).fetchone()
        if not binding or binding.get("stage") != "dummy":
            raise PermissionError("original canonical dummy stage required")
        await asyncio.to_thread(require_current_stage, self.outbox, binding)
        return run

    @activity.defn(name="salience.external_effect")
    async def external_effect(self, request: DummyWorkflowRequest):
        run = await self._run()
        expected = DummyWorkflowRequest(idempotency_key="v4-legacy:"+str(binding_for_job(self.outbox, run.job_id)["operation_id"]), mode="success", dry_run=True)
        if request != expected:
            raise PermissionError("original fixed no-send dummy payload required")
        return await super().external_effect(request)


def binding_for_job(outbox, job_id):
    with outbox._connect() as connection:
        row = connection.execute("SELECT * FROM v4_legacy_dispatches WHERE job_id=%s AND workspace_id=%s", (job_id, outbox.workspace_id)).fetchone()
        if not row:
            raise PermissionError("original scoped legacy job required")
        return row


class LegacyNoSendCreativeProvider(FixtureCreativeProvider):
    async def submit(self, *args, **kwargs):
        raise PermissionError("legacy creative never sends a provider effect")

    async def reconcile(self, *args, **kwargs):
        raise PermissionError("legacy creative never reconciles a provider effect")

    async def get_status(self, *args, **kwargs):
        raise PermissionError("legacy creative never polls a provider effect")

    async def download(self, *args, **kwargs):
        raise PermissionError("legacy creative never downloads a provider asset")

    async def cancel(self, *args, **kwargs):
        raise PermissionError("legacy creative never cancels a provider effect")


class GuardedCreativeActivities(CreativeActivities):
    def __init__(self, state, outbox):
        super().__init__(state)
        self.outbox = outbox

    async def _run(self):
        run = await super()._run()
        binding = await asyncio.to_thread(binding_for_job, self.outbox, run.job_id)
        if binding.get("stage") != "creative":
            raise PermissionError("original canonical creative stage required")
        # Ordinary close revokes execution, while its physical cancellation
        # must still project the original dry job's terminal state.
        if activity.info().activity_type != "salience.creative.cancel":
            await asyncio.to_thread(require_current_stage, self.outbox, binding)
        return run

    async def _guard(self, payload):
        run = await self._run()
        binding = await asyncio.to_thread(binding_for_job, self.outbox, run.job_id)
        expected = legacy_execution(binding)[2]
        if payload["request"] != {key: value for key, value in expected.__dict__.items() if key != "contract_version"}:
            raise PermissionError("original fixed no-send creative payload required")
        brief = await self._state.intelligence_repository.exact_content_brief(
            brief_id=expected.brief_id, workspace_id=expected.workspace_id, program_id=expected.content_program_id)
        if payload["brief"] != brief or ("provider_state" in payload and payload["provider_state"] != "dry_run"):
            raise PermissionError("original scoped dry creative input required")

    @activity.defn(name="salience.creative.load_brief")
    async def load_brief(self, request: CreativeProductionRequest) -> dict[str, Any]:
        run = await self._run()
        binding = await asyncio.to_thread(binding_for_job, self.outbox, run.job_id)
        if request != legacy_execution(binding)[2]:
            raise PermissionError("original fixed no-send creative payload required")
        return await super().load_brief(request)

    @activity.defn(name="salience.creative.cancel")
    async def cancel(self, payload: dict[str, Any] | None = None):
        await self._run()
        if payload is not None:
            await self._guard(payload)
        return await super().cancel(payload)

    @activity.defn(name="salience.creative.script")
    async def script(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().script(payload)

    @activity.defn(name="salience.creative.verify_script")
    async def verify_script(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().verify_script(payload)

    @activity.defn(name="salience.creative.direction")
    async def direction(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().direction(payload)

    @activity.defn(name="salience.creative.authorize")
    async def authorize(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().authorize(payload)

    @activity.defn(name="salience.creative.submit_or_reconcile")
    async def submit_or_reconcile(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().submit_or_reconcile(payload)

    @activity.defn(name="salience.creative.await_provider")
    async def await_provider(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().await_provider(payload)

    @activity.defn(name="salience.creative.import_validate")
    async def import_validate(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().import_validate(payload)

    @activity.defn(name="salience.creative.distribute")
    async def distribute(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().distribute(payload)

    @activity.defn(name="salience.creative.final_gate")
    async def final_gate(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().final_gate(payload)

    @activity.defn(name="salience.creative.complete")
    async def complete(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().complete(payload)

    @activity.defn(name="salience.creative.denied")
    async def denied(self, payload: dict[str, Any]):
        await self._guard(payload)
        return await super().denied(payload)


class LegacyScheduleActivities:
    def __init__(self, client, outbox, task_queue):
        self.client, self.outbox, self.task_queue = client, outbox, task_queue

    @activity.defn(name="salience.v4.legacy_schedule_admit")
    async def admit(self, payload: dict) -> dict:
        from salience.cycles.legacy_dispatch import LegacyDispatch
        from salience.cycles.authority import current_authority
        from temporalio.exceptions import ApplicationError
        from temporalio.client import ScheduleActionExecutionStartWorkflow
        require_fixture()
        try:
            if set(payload) != {"workspace_id", "content_program_id", "goal_id", "schedule_id", "dry_run", "scheduled_at", "remote_id"} or payload["workspace_id"] != str(self.outbox.workspace_id) or payload["dry_run"] is not True:
                raise PermissionError("scoped no-effects schedule payload required")
            with self.outbox._connect() as connection:
                plan = connection.execute("SELECT * FROM v4_legacy_schedule_plans WHERE goal_id=%s AND workspace_id=%s", (payload["goal_id"], self.outbox.workspace_id)).fetchone()
                if not plan or plan["task_queue"] != self.task_queue:
                    raise PermissionError("original scoped worker plan required")
                current_authority(connection, self.outbox.workspace_id, plan["actor_id"], "cycles:schedule")
                row = schedule_row(connection, payload["schedule_id"], self.outbox.workspace_id, temporal=True)
                require_native_binding(row, goal_id=payload["goal_id"], actor_id=plan["actor_id"], task_queue=self.task_queue)
                if str(row["content_program_id"]) != payload["content_program_id"] or payload["remote_id"] != schedule_metadata(row)["remote_id"]:
                    raise PermissionError("original program and remote schedule required")
            slot = datetime.fromisoformat(payload["scheduled_at"])
            description = await self.client.get_schedule_handle(payload["remote_id"]).describe(rpc_timeout=timedelta(seconds=2))
            invocation = activity.info()
            if not any(isinstance(result.action, ScheduleActionExecutionStartWorkflow) and result.action.workflow_id == invocation.workflow_id and result.action.first_execution_run_id == invocation.workflow_run_id and result.scheduled_at == slot for result in description.info.recent_actions):
                raise RuntimeError("original scheduled action readback pending")
            bridge = LegacyDispatch(self.outbox.database_url, workspace_id=self.outbox.workspace_id,
                subject_id=plan["actor_id"], task_queue=self.task_queue)
            return await asyncio.to_thread(bridge.admit_legacy_tick, payload["goal_id"],
                schedule_id=payload["schedule_id"], scheduled_at=slot, remote_id=payload["remote_id"])
        except (PermissionError, ValueError) as error:
            raise ApplicationError(str(error), non_retryable=True) from error


def build_legacy_worker(client, *, task_queue, outbox):
    require_fixture()
    if outbox.delivery_lane != "legacy":
        raise ValueError("legacy delivery lane required")
    adapter = LegacyIntelligenceAdapter(client,outbox,task_queue=task_queue)
    cycles = LegacyCycleActivities(outbox,adapter=adapter)
    state = IntelligenceWorkflowState(store=CanonicalJobStore(outbox.database_url),
        repository=IntelligenceRepository(outbox.database_url),agents=fixture_agent_service())
    intelligence = GuardedIntelligenceActivities(state,outbox)
    schedule = LegacyScheduleActivities(client, outbox, task_queue)
    dummy = GuardedDummyActivities(WorkflowScenarioState(store=CanonicalJobStore(outbox.database_url), provider=LegacyNoSendProvider()), outbox)
    creative = GuardedCreativeActivities(CreativeWorkflowState(store=CanonicalJobStore(outbox.database_url),
        intelligence_repository=IntelligenceRepository(outbox.database_url), creative_repository=CreativeRepository(outbox.database_url),
        agents=fixture_agent_service(), provider=LegacyNoSendCreativeProvider()), outbox)
    return Worker(client,task_queue=task_queue,workflows=[LocalCycleWorkflow,IntelligenceLoopWorkflow,DurableDummyWorkflow,CreativeProductionWorkflow,LegacyScheduledIngressWorkflow],
        activities=[cycles.consume,cycles.runtime_binding,cycles.runtime_hold,
                    schedule.admit, dummy.checkpoint, dummy.external_effect, dummy.terminal, dummy.dead_letter,
                    intelligence.fetch,intelligence.normalize,intelligence.rank,intelligence.strategy,
                    intelligence.queue,intelligence.complete,intelligence.cancel,intelligence.packages,
                    intelligence.claims,intelligence.brief,
                    creative.load_brief,creative.script,creative.verify_script,creative.direction,creative.authorize,
                    creative.submit_or_reconcile,creative.await_provider,creative.import_validate,creative.distribute,
                    creative.final_gate,creative.complete,creative.denied,creative.cancel],
        max_concurrent_activities=2,max_concurrent_workflow_tasks=2)


async def main():
    require_fixture()
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = os.environ["V4_LEGACY_FIXTURE_QUEUE"]
    outbox = CycleOutbox(os.environ["TEST_DATABASE_URL"],workspace_id=os.environ["V4_FIXTURE_WORKSPACE"],delivery_lane="legacy")
    async with build_legacy_worker(client,task_queue=queue,outbox=outbox):
        async with asyncio.TaskGroup() as group:
            stop = asyncio.Event()
            group.create_task(outbox.run_until_stopped(TemporalCycleTransport(client,task_queue=queue),stop_event=stop))
            if os.environ.get("V4_FIXTURE_AUTOSCHEDULE") == "1":
                control = None
                if os.environ.get("V4_LEGACY_TEMPORAL_SCHEDULES","false") == "true":
                    from salience.cycles.temporal_schedule_control import TemporalFixtureLegacyScheduleControl
                    control = TemporalFixtureLegacyScheduleControl(outbox.database_url,workspace_id=outbox.workspace_id,
                        temporal_target=os.environ["TEST_TEMPORAL_TARGET"],task_queue=queue)
                poller = FixtureSchedulePoller(outbox.database_url,workspace_id=outbox.workspace_id,legacy_control=control)
                group.create_task(poller.run_until_stopped(stop_event=stop))


if __name__ == "__main__":
    asyncio.run(main())
