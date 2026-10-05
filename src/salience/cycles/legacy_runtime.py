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
from salience.workflows.persistence import CanonicalJobStore


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
        workspace_stop, goal_stop = service._require_running(connection,binding["goal_id"])
        claim = connection.execute("""SELECT claim.*,permit.expires_at,permit.subject_id,
            permit.workspace_stop_revision,permit.goal_stop_revision FROM v4_permit_claims claim
            JOIN v4_dispatch_permits permit ON permit.id=claim.current_permit_id
            WHERE claim.cycle_id=%s""",(binding["cycle_id"],)).fetchone()
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        if not claim or claim["state"] != "claimed" or claim["operation_id"] != binding["operation_id"] or claim["subject_id"] != binding["actor_id"] or now >= claim["expires_at"] or (workspace_stop["revision"],goal_stop["revision"]) != (claim["workspace_stop_revision"],claim["goal_stop_revision"]):
            raise PermissionError("current original legacy permit required")


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
        payload = IntelligenceLoopRequest(workspace_id=str(binding["workspace_id"]),content_program_id=str(binding["content_program_id"]),
            niche=binding["payload"]["niche"],idempotency_key="v4-legacy:"+operation_id,dry_run=True,
            selected_opportunity_id=binding["payload"].get("selected_opportunity_id"))
        try:
            await self.client.start_workflow(IntelligenceLoopWorkflow.run,payload,id=workflow_id,
                task_queue=self.task_queue,id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=timedelta(minutes=5),memo=memo,rpc_timeout=timedelta(seconds=2))
        except WorkflowAlreadyStartedError:
            description = await self.client.get_workflow_handle(workflow_id).describe(rpc_timeout=timedelta(seconds=2))
            if description.workflow_type != INTELLIGENCE_WORKFLOW_TYPE or description.task_queue != self.task_queue or await description.memo() != memo:
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
            if description.workflow_type != INTELLIGENCE_WORKFLOW_TYPE or description.task_queue != binding["task_queue"] or await description.memo() != expected:
                raise PermissionError("original legacy cancellation target required")
            from temporalio.client import WorkflowExecutionStatus
            if description.status != WorkflowExecutionStatus.RUNNING:
                return result
            try:
                await handle.signal(IntelligenceLoopWorkflow.request_cancellation,rpc_timeout=timedelta(seconds=2))
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
        if not binding:
            raise PermissionError("only canonical legacy jobs are supported")
        if activity.info().activity_type != "salience.intelligence.cancel":
            await asyncio.to_thread(require_current_stage,self.outbox,binding)
        return run


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
    return Worker(client,task_queue=task_queue,workflows=[LocalCycleWorkflow,IntelligenceLoopWorkflow,LegacyScheduledIngressWorkflow],
        activities=[cycles.consume,cycles.runtime_binding,cycles.runtime_hold,
                    schedule.admit,
                    intelligence.fetch,intelligence.normalize,intelligence.rank,intelligence.strategy,
                    intelligence.queue,intelligence.complete,intelligence.cancel,intelligence.packages,
                    intelligence.claims,intelligence.brief],
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
