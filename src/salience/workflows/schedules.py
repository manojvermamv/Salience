"""Owned Temporal scheduling boundary for durable jobs."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)


class TemporalScheduleClient(Protocol):
    async def create_schedule(self, schedule_id: str, schedule: Schedule) -> None: ...


@dataclass(frozen=True)
class ScheduleRequest:
    schedule_id: str
    task_queue: str
    every: timedelta
    workflow_type: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.schedule_id:
            raise ValueError("schedule_id must be non-empty")
        if not self.task_queue:
            raise ValueError("task_queue must be non-empty")
        if not self.workflow_type:
            raise ValueError("workflow_type must be non-empty")
        if self.every <= timedelta():
            raise ValueError("schedule interval must be positive")


class TemporalScheduleService:
    def __init__(self, client: TemporalScheduleClient) -> None:
        self._client = client

    async def create_every(self, request: ScheduleRequest) -> str:
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                request.workflow_type,
                request.payload,
                id=f"{request.schedule_id}:execution",
                task_queue=request.task_queue,
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=request.every)]),
        )
        await self._client.create_schedule(request.schedule_id, schedule)
        return request.schedule_id
