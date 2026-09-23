"""Explicit local fixture worker; never registered in the production worker."""

import asyncio
from datetime import timedelta
import os

from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from salience.cycles.outbox import CycleOutbox
from salience.cycles.workflow import LocalCycleWorkflow


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
    def __init__(self, outbox):
        self.outbox = outbox

    @activity.defn(name="salience.v4.fixture_consume")
    async def consume(self, message: dict) -> dict:
        receipt = await asyncio.to_thread(self.outbox.consume,message["message_id"],traceparent=message["traceparent"],expected_cycle_id=message["cycle_id"],expected_kind=message["kind"])
        if str(receipt["cycle_id"]) != message["cycle_id"]:
            raise ValueError("message belongs to another cycle")
        return {"state":receipt["state"],"cycle_id":str(receipt["cycle_id"])}


def build_local_cycle_worker(client, *, task_queue, outbox):
    TemporalCycleTransport(client,task_queue=task_queue)
    activities = LocalCycleActivities(outbox)
    return Worker(client,task_queue=task_queue,workflows=[LocalCycleWorkflow],activities=[activities.consume],max_concurrent_activities=2,max_concurrent_workflow_tasks=2)


async def main():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    outbox = CycleOutbox(os.environ["TEST_DATABASE_URL"],workspace_id=os.environ["V4_FIXTURE_WORKSPACE"])
    worker = build_local_cycle_worker(client,task_queue=os.environ["V4_FIXTURE_QUEUE"],outbox=outbox)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
