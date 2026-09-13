"""Canonical-reference Temporal scheduling contracts for publication."""

from datetime import timedelta

import pytest

from salience.workflows.publication import (
    PublicationScheduleRequest,
    PublicationScheduleService,
)


class _ScheduleClient:
    def __init__(self) -> None:
        self.schedule_id: str | None = None
        self.schedule: object | None = None

    async def create_schedule(self, schedule_id: str, schedule: object) -> None:
        self.schedule_id = schedule_id
        self.schedule = schedule


@pytest.mark.asyncio
async def test_publication_schedule_sends_only_its_canonical_schedule_identity() -> None:
    client = _ScheduleClient()
    request = PublicationScheduleRequest(
        publication_schedule_id="publication-schedule-1",
        name="weekday-private-release",
        every=timedelta(hours=24),
    )

    schedule_id = await PublicationScheduleService(client, task_queue="publication-queue").create_every(
        request
    )

    assert schedule_id == "publication:publication-schedule-1"
    assert client.schedule_id == schedule_id
    assert client.schedule is not None
    action = client.schedule.action
    assert action.args == [
        {
            "scheduled_publication_schedule_id": "publication-schedule-1",
            "contract_version": "PublicationWorkflowRequest@v1",
        }
    ]
