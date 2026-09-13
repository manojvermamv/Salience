"""Immutable-reference Temporal scheduling contracts for publication."""

from datetime import timedelta

import pytest

from salience.workflows.publication import (
    PublicationScheduleRequest,
    PublicationScheduleService,
    PublicationWorkflowRequest,
    _workflow_request_matches_publication,
)
from salience.publication.contracts import PublicationRequest


class _ScheduleClient:
    def __init__(self) -> None:
        self.schedule_id: str | None = None
        self.schedule: object | None = None

    async def create_schedule(self, schedule_id: str, schedule: object) -> None:
        self.schedule_id = schedule_id
        self.schedule = schedule


@pytest.mark.asyncio
async def test_publication_schedule_uses_versioned_request_and_plan_identity() -> None:
    client = _ScheduleClient()
    request = PublicationScheduleRequest(
        publication_request_id="publication-request-1",
        publication_plan_id="publication-plan-1",
        schedule_version=1,
        name="weekday-private-release",
        every=timedelta(hours=24),
        workflow_request=PublicationWorkflowRequest(
            workspace_id="workspace-1",
            content_program_id="program-1",
            ready_package_id="ready-package-1",
            publisher_account_id="publisher-account-1",
            budget_id="budget-1",
            idempotency_key="publication-schedule-1",
        ),
    )

    schedule_id = await PublicationScheduleService(client, task_queue="publication-queue").create_every(
        request
    )

    assert schedule_id == "publication:publication-request-1:publication-plan-1:1"
    assert client.schedule_id == schedule_id


def test_scheduled_workflow_rejects_payload_that_differs_from_its_immutable_request() -> None:
    workflow_request = PublicationWorkflowRequest(
        workspace_id="workspace-1",
        content_program_id="program-1",
        ready_package_id="ready-package-1",
        publisher_account_id="publisher-account-1",
        budget_id="budget-1",
        idempotency_key="publication-schedule-1",
        destination="fixture://tampered-target",
        scheduled_publication_request_id="publication-request-1",
        scheduled_publication_plan_id="publication-plan-1",
    )
    canonical_request = PublicationRequest(
        id="publication-request-1",
        workspace_id="workspace-1",
        content_program_id="program-1",
        ready_package_id="ready-package-1",
        publisher_account_id="publisher-account-1",
        platform="fixture",
        destination="fixture://canonical-target",
        locale="en",
        territory="global",
        visibility="private",
        capability_profile_version=1,
        idempotency_key="publication-schedule-1",
        approval_reference="ready-package:ready-package-1",
        publisher_id="fixture-publisher",
    )

    assert _workflow_request_matches_publication(workflow_request, canonical_request) is False
