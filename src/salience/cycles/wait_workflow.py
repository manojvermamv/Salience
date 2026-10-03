"""Finite Temporal timer; canonical decisions stay in activities."""
import asyncio
from datetime import datetime, timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn(name="SalienceLocalWaitWorkflow")
class LocalWaitWorkflow:
    @workflow.run
    async def run(self, job_id: str):
        options = dict(start_to_close_timeout=timedelta(seconds=5),schedule_to_close_timeout=timedelta(seconds=20),
                       retry_policy=RetryPolicy(initial_interval=timedelta(milliseconds=100),maximum_interval=timedelta(seconds=1),maximum_attempts=3))
        binding = await workflow.execute_activity("salience.v4.fixture_wait_binding",job_id,**options)
        if binding["state"] == "pending":
            remaining = datetime.fromisoformat(binding["deadline"])-workflow.now()
            if remaining.total_seconds()>0:
                await asyncio.sleep(remaining.total_seconds())
        return await workflow.execute_activity("salience.v4.fixture_wait_fire",job_id,**options)
