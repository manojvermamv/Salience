"""Bounded Temporal schedule ingress; the activity commits canonical outbox."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, SearchAttributeKey
from temporalio.exceptions import ApplicationError


LEGACY_SCHEDULE_INGRESS = "SalienceLegacyScheduledIngress"


@workflow.defn(name=LEGACY_SCHEDULE_INGRESS)
class LegacyScheduledIngressWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        attributes = workflow.info().typed_search_attributes
        slot = attributes.get(SearchAttributeKey.for_datetime("TemporalScheduledStartTime"))
        remote_id = attributes.get(SearchAttributeKey.for_keyword("TemporalScheduledById"))
        if slot is None or remote_id is None:
            raise ApplicationError("Actual Temporal scheduled invocation required", non_retryable=True)
        return await workflow.execute_activity("salience.v4.legacy_schedule_admit",
            payload | {"scheduled_at": slot.isoformat(), "remote_id": remote_id},
            start_to_close_timeout=timedelta(seconds=10),
            schedule_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3))
