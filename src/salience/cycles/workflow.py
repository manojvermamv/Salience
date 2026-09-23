from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn(name="SalienceLocalCycleWorkflow")
class LocalCycleWorkflow:
    def __init__(self):
        self.pending = []
        self.overflow = False

    @workflow.signal
    def deliver(self, message: dict):
        if len(self.pending) >= 32:
            self.overflow = True
        else:
            self.pending.append(message)

    async def consume(self, message):
        return await workflow.execute_activity(
            "salience.v4.fixture_consume",
            message,
            start_to_close_timeout=timedelta(seconds=5),
            schedule_to_close_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(initial_interval=timedelta(milliseconds=100),maximum_interval=timedelta(seconds=1),maximum_attempts=3),
        )

    @workflow.run
    async def run(self, initial: dict):
        await self.consume(initial)
        for _ in range(31):
            try:
                await workflow.wait_condition(lambda: bool(self.pending) or self.overflow,timeout=timedelta(minutes=5))
            except TimeoutError:
                return {"cycle_id":initial["cycle_id"],"state":"held_timeout"}
            if self.overflow:
                return {"cycle_id":initial["cycle_id"],"state":"held_message_limit"}
            message = self.pending.pop(0)
            if message["cycle_id"] != initial["cycle_id"]:
                continue
            receipt = await self.consume(message)
            if message["kind"] == "close":
                return {"cycle_id":initial["cycle_id"],"state":receipt["state"]}
        return {"cycle_id":initial["cycle_id"],"state":"held_message_limit"}
