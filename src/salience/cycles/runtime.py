"""Explicit local fixture worker; never registered in the production worker."""

import asyncio
from datetime import timedelta
from hashlib import sha256
import os

from opentelemetry import trace
from uuid import uuid4
from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from salience.cycles.governance import CycleGovernance, PermitRequest
from salience.cycles.outbox import CycleOutbox
from salience.cycles.schedule_cutover import FixtureSchedulePoller
from salience.cycles.workflow import LocalCycleWorkflow
from salience.cycles.runtime_waits import RuntimeWaits, WaitActivities, FixtureWaitDriver
from salience.cycles.wait_workflow import LocalWaitWorkflow
from salience.observability.tracing import OpenTelemetryTraceEmitter, TraceContext


class TemporalCycleTransport:
    def __init__(self, client, *, task_queue):
        if not task_queue.startswith("salience-v4-local-") or os.environ.get("SALIENCE_DEPLOYMENT_MODE","fixture") != "fixture":
            raise ValueError("only an isolated local fixture queue is supported")
        self.client = client
        self.task_queue = task_queue
        self._reconcile_after = None

    @staticmethod
    def workflow_id(cycle_id):
        return "salience-v4-local:" + str(cycle_id)

    async def reconcile_one(self, outbox):
        message = await asyncio.to_thread(outbox.runtime_candidate,self._reconcile_after)
        if message is None:
            return False
        self._reconcile_after = message['id']
        if await asyncio.to_thread(outbox.runtime_held,message['cycle_id']):
            return bool(await asyncio.to_thread(outbox.reconcile_held_receipts,message['cycle_id']))
        try:
            description = await self.client.get_workflow_handle(self.workflow_id(message['cycle_id'])).describe(rpc_timeout=timedelta(seconds=3))
            memo = await description.memo()
        except Exception:
            # Observation failure never proves terminal failure. Retry bounded
            # scanning on the next poll after database/Temporal recovery.
            return False
        if description.workflow_type != 'SalienceLocalCycleWorkflow' or memo.get('cycle_message_id') != str(message['id']):
            return False
        if description.status in {WorkflowExecutionStatus.FAILED,WorkflowExecutionStatus.TIMED_OUT,WorkflowExecutionStatus.TERMINATED,WorkflowExecutionStatus.CANCELED}:
            held = await asyncio.to_thread(outbox.hold_failed_runtime,message['id'],description.status.name)
            await asyncio.to_thread(outbox.reconcile_held_receipts,message['cycle_id'])
            return held
        return False

    async def deliver(self, message):
        envelope = {"message_id":str(message["id"]),"cycle_id":str(message["cycle_id"]),"kind":message["kind"],"traceparent":message.get("delivery_traceparent",message["traceparent"])}
        workflow_id = self.workflow_id(message["cycle_id"])
        if message["kind"] == "start":
            try:
                await self.client.start_workflow(LocalCycleWorkflow.run,envelope,id=workflow_id,task_queue=self.task_queue,id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,execution_timeout=timedelta(hours=1),memo={"cycle_message_id":envelope["message_id"]},rpc_timeout=timedelta(seconds=3))
            except WorkflowAlreadyStartedError:
                description = await self.client.get_workflow_handle(workflow_id).describe(rpc_timeout=timedelta(seconds=3))
                memo = await description.memo()
                if description.workflow_type != "SalienceLocalCycleWorkflow" or memo.get("cycle_message_id") != envelope["message_id"]:
                    raise ValueError("runtime identity already belongs to different work")
        elif message["kind"] in {"recovery","close"}:
            await self.client.get_workflow_handle(workflow_id).signal(LocalCycleWorkflow.deliver,envelope,rpc_timeout=timedelta(seconds=3))
        else:
            raise ValueError("unsupported local message kind")


class LocalCycleActivities:
    def __init__(self, outbox, *, adapter=None, tracer=None):
        if adapter is not None and (os.environ.get("SALIENCE_DEPLOYMENT_MODE") != "fixture" or getattr(adapter,"effect_id",None) != "fixture.noop"):
            raise ValueError("only an explicitly isolated no-effects fixture adapter is allowed")
        self.outbox = outbox
        self.adapter = adapter
        self.emitter = OpenTelemetryTraceEmitter(tracer or trace.get_tracer("salience.cycles"))

    @activity.defn(name="salience.v4.fixture_consume")
    async def consume(self, message: dict) -> dict:
        receipt = await asyncio.to_thread(self.outbox.consume,message["message_id"],traceparent=message["traceparent"],expected_cycle_id=message["cycle_id"],expected_kind=message["kind"])
        if str(receipt["cycle_id"]) != message["cycle_id"]:
            raise ValueError("message belongs to another cycle")
        state = receipt["state"]
        if self.adapter is not None and message["kind"] == "start" and state == "recorded":
            binding = await asyncio.to_thread(self.outbox.fixture_binding,message["message_id"])
            context = TraceContext.from_carrier({"traceparent":receipt["traceparent"]})
            governance = CycleGovernance(self.outbox.database_url,workspace_id=self.outbox.workspace_id,subject_id=binding["subject_id"],trace_context=context)
            permit = PermitRequest(context_id=binding["context_id"],operation_id=binding["operation_id"],expected_goal_revision=binding["goal_revision"],account_ref="fixture-account",purpose="fixture_execution",effect="fixture.noop",artifact_sha256=sha256(str(message["message_id"]).encode()).hexdigest(),ttl_seconds=30)
            issued = await asyncio.to_thread(governance.issue_permit,binding["cycle_id"],permit,idempotency_key="fixture-adapter:"+str(message["message_id"]))
            claim = await asyncio.to_thread(governance.claim_permit,issued["permit_id"])
            if claim["dispatch_allowed"]:
                try:
                    with self.emitter.active_span(context,"cycle.fixture.adapter") as emitted:
                        operation_id = str(binding["operation_id"])
                        async with asyncio.timeout(3):
                            result = await self.adapter.invoke(operation_id=operation_id,idempotency_key=operation_id,traceparent=emitted.to_carrier()["traceparent"])
                        if result != {"accepted":True,"operation_id":operation_id,"idempotency_key":operation_id}:
                            raise ValueError("fixture adapter acceptance binding mismatch")
                        await asyncio.to_thread(self.outbox.record_fixture_acceptance,message["message_id"],binding["operation_id"],result,emitted.to_carrier()["traceparent"])
                except Exception:
                    await asyncio.to_thread(governance.mark_unknown,issued["permit_id"])
                    raise
            elif not await asyncio.to_thread(self.outbox.accepted_fixture,message["message_id"],binding["operation_id"]):
                state = "held"
        return {"state":state,"cycle_id":str(receipt["cycle_id"])}


    @activity.defn(name="salience.v4.fixture_runtime_binding")
    async def runtime_binding(self, message: dict) -> dict:
        def read():
            binding = self.outbox.fixture_binding(message["message_id"])
            if str(binding["cycle_id"]) != message["cycle_id"]:
                raise PermissionError("runtime start binding mismatch")
            with self.outbox._connect() as connection:
                context = connection.execute("SELECT payload,created_at FROM v4_run_contexts WHERE id=%s", (binding["context_id"],)).fetchone()
            deadline = context["payload"].get("execution_deadline") or (context["created_at"]+timedelta(minutes=5)).isoformat()
            return {"cycle_id":str(binding["cycle_id"]),"context_id":str(binding["context_id"]),"operation_id":str(binding["operation_id"]),"deadline":deadline}
        return await asyncio.to_thread(read)

    @activity.defn(name="salience.v4.fixture_runtime_hold")
    async def runtime_hold(self, message: dict) -> dict:
        def hold():
            with self.outbox._connect() as connection:
                # Serialize with close/permit through the canonical goal lock.
                row = connection.execute("""SELECT c.*,i.goal_id,g.workspace_id,o.subject_id FROM v4_cycles c
                    JOIN v4_cycle_intents i ON i.id=c.intent_id JOIN v4_goals g ON g.id=i.goal_id
                    JOIN v4_cycle_outbox o ON o.cycle_id=c.id AND o.kind='start' AND o.sequence=1
                    WHERE c.id=%s AND g.workspace_id=%s FOR UPDATE OF g,c""", (message["cycle_id"],self.outbox.workspace_id)).fetchone()
                if not row or str(row["context_id"]) != message["context_id"] or str(row["operation_id"]) != message["operation_id"]:
                    raise PermissionError("runtime hold identity mismatch")
                inserted = connection.execute("""INSERT INTO v4_runtime_holds(cycle_id,context_id,operation_id,workspace_id,owner_id,reason)
                    VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(cycle_id) DO NOTHING RETURNING cycle_id""", (row["id"],row["context_id"],row["operation_id"],self.outbox.workspace_id,row["subject_id"],message["state"])).fetchone()
                if inserted:
                    connection.execute("""INSERT INTO v4_cycle_events(id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,traceparent,payload)
                        VALUES(%s,%s,%s,%s,%s,%s,'runtime_held',%s,%s)""", (uuid4(),self.outbox.workspace_id,row["subject_id"],row["goal_id"],row["intent_id"],row["id"],TraceContext.new_root().to_carrier()["traceparent"],Jsonb(message)))
            return message
        return await asyncio.to_thread(hold)


def build_local_cycle_worker(client, *, task_queue, outbox, adapter=None, tracer=None, waits=None):
    TemporalCycleTransport(client,task_queue=task_queue)
    activities = LocalCycleActivities(outbox,adapter=adapter,tracer=tracer)
    registered = [activities.consume,activities.runtime_binding,activities.runtime_hold]
    workflows = [LocalCycleWorkflow]
    if waits is not None:
        if os.environ.get("SALIENCE_DEPLOYMENT_MODE") != "fixture" or waits.workspace_id != outbox.workspace_id:
            raise ValueError("same-workspace explicit fixture waits required")
        deadline_activities = WaitActivities(waits)
        registered += [deadline_activities.binding,deadline_activities.fire]
        workflows += [LocalWaitWorkflow]
    return Worker(client,task_queue=task_queue,workflows=workflows,activities=registered,max_concurrent_activities=2,max_concurrent_workflow_tasks=2)


async def main():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    outbox = CycleOutbox(os.environ["TEST_DATABASE_URL"],workspace_id=os.environ["V4_FIXTURE_WORKSPACE"])
    automatic_waits = os.environ.get("V4_FIXTURE_AUTOWAITS") == "1"
    waits = RuntimeWaits(os.environ["TEST_DATABASE_URL"],workspace_id=outbox.workspace_id,subject_id=os.environ["V4_FIXTURE_WAIT_OPERATOR"]) if automatic_waits else None
    worker = build_local_cycle_worker(client,task_queue=os.environ["V4_FIXTURE_QUEUE"],outbox=outbox,waits=waits)
    automatic_dispatch = os.environ.get("V4_FIXTURE_AUTODISPATCH") == "1"
    automatic_schedule = os.environ.get("V4_FIXTURE_AUTOSCHEDULE") == "1"
    if automatic_dispatch or automatic_schedule or automatic_waits:
        if os.environ.get("SALIENCE_DEPLOYMENT_MODE") != "fixture":
            raise ValueError("automatic local work requires explicit fixture mode")
        async with worker:
            async with asyncio.TaskGroup() as group:
                stop_event = asyncio.Event()
                if automatic_dispatch:
                    transport = TemporalCycleTransport(client,task_queue=os.environ["V4_FIXTURE_QUEUE"])
                    group.create_task(outbox.run_until_stopped(transport,stop_event=stop_event))
                if automatic_waits:
                    driver = FixtureWaitDriver(client,waits,task_queue=os.environ["V4_FIXTURE_QUEUE"])
                    group.create_task(driver.run_until_stopped(stop_event=stop_event))
                if automatic_schedule:
                    poller = FixtureSchedulePoller(os.environ["TEST_DATABASE_URL"],workspace_id=os.environ["V4_FIXTURE_WORKSPACE"])
                    group.create_task(poller.run_until_stopped(stop_event=stop_event))
    else:
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
