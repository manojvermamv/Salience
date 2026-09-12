from datetime import timedelta

import pytest

from salience.workflows.schedules import ScheduleRequest, TemporalScheduleService


class RecordingScheduleClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, object]] = []

    async def create_schedule(self, schedule_id: str, schedule: object) -> None:
        self.created.append((schedule_id, schedule))


@pytest.mark.asyncio
async def test_schedule_adapter_uses_temporal_interval_and_canonical_identifier() -> None:
    client = RecordingScheduleClient()
    service = TemporalScheduleService(client)

    schedule_id = await service.create_every(
        ScheduleRequest(
            schedule_id="workspace:daily-dummy",
            task_queue="salience-phase-one",
            every=timedelta(hours=24),
            workflow_type="DurableDummyWorkflow",
            payload={"dry_run": True},
        )
    )

    assert schedule_id == "workspace:daily-dummy"
    assert client.created[0][0] == schedule_id


def test_schedule_adapter_rejects_nonpositive_intervals() -> None:
    with pytest.raises(ValueError, match="positive"):
        ScheduleRequest(
            schedule_id="invalid",
            task_queue="salience-phase-one",
            every=timedelta(),
            workflow_type="DurableDummyWorkflow",
            payload={},
        )
