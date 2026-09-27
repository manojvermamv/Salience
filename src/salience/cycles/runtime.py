"""Explicit local fixture worker; never registered in the production worker."""

import asyncio
from datetime import timedelta
from hashlib import sha256
import os

from opentelemetry import trace
from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from salience.cycles.governance import CycleGovernance, PermitRequest
from salience.cycles.outbox import CycleOutbox
from salience.cycles.schedule_cutover import FixtureSchedulePoller
from salience.cycles.workflow import LocalCycleWorkflow
from salience.observability.tracing import OpenTelemetryTraceEmitter, TraceContext


class TemporalCycleTransport:
    def __init__(self, client, *, task_queue):
        if not task_queue.startswith("salience-v4-local-") or os.environ.get("SALIENCE_DEPLOYMENT_MODE","fixture") != "fixture":
            raise ValueError("only an isolated local fixture queue is supported")
        self.client = client
        self.task_queue = task_queue

    @staticmethod
    def workflow_id(cycle_id):
        return "salience-v4-local:" + str(cycle_id)

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


def build_local_cycle_worker(client, *, task_queue, outbox, adapter=None, tracer=None):
    TemporalCycleTransport(client,task_queue=task_queue)
    activities = LocalCycleActivities(outbox,adapter=adapter,tracer=tracer)
    return Worker(client,task_queue=task_queue,workflows=[LocalCycleWorkflow],activities=[activities.consume],max_concurrent_activities=2,max_concurrent_workflow_tasks=2)


async def main():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    outbox = CycleOutbox(os.environ["TEST_DATABASE_URL"],workspace_id=os.environ["V4_FIXTURE_WORKSPACE"])
    worker = build_local_cycle_worker(client,task_queue=os.environ["V4_FIXTURE_QUEUE"],outbox=outbox)
    automatic_dispatch = os.environ.get("V4_FIXTURE_AUTODISPATCH") == "1"
    automatic_schedule = os.environ.get("V4_FIXTURE_AUTOSCHEDULE") == "1"
    if automatic_dispatch or automatic_schedule:
        if os.environ.get("SALIENCE_DEPLOYMENT_MODE") != "fixture":
            raise ValueError("automatic local work requires explicit fixture mode")
        async with worker:
            async with asyncio.TaskGroup() as group:
                stop_event = asyncio.Event()
                if automatic_dispatch:
                    transport = TemporalCycleTransport(client,task_queue=os.environ["V4_FIXTURE_QUEUE"])
                    group.create_task(outbox.run_until_stopped(transport,stop_event=stop_event))
                if automatic_schedule:
                    poller = FixtureSchedulePoller(os.environ["TEST_DATABASE_URL"],workspace_id=os.environ["V4_FIXTURE_WORKSPACE"])
                    group.create_task(poller.run_until_stopped(stop_event=stop_event))
    else:
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
