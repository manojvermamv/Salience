from typing import Any

from temporalio.client import Client, WorkflowHandle

from salience.contracts.workflow import WorkflowBackend


class TemporalWorkflowBackend(WorkflowBackend):
    def __init__(self, client: Client, *, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    async def start(self, workflow_type: str, workflow_id: str, payload: dict[str, Any]) -> str:
        await self._client.start_workflow(
            workflow_type,
            payload,
            id=workflow_id,
            task_queue=self._task_queue,
        )
        return workflow_id

    async def status(self, workflow_id: str) -> str:
        description = await self._client.describe_workflow(workflow_id)
        return str(description.status)

    async def cancel(self, workflow_id: str) -> None:
        await self._client.get_workflow_handle(workflow_id).cancel()

    async def resume(self, workflow_id: str) -> None:
        await self._client.get_workflow_handle(workflow_id).signal("resume")

