from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError


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
        # Keep the original command sequence for histories without this marker.
        if workflow.patched("v4-item6-bounded-runtime"):
            return await self.bounded_run(initial)
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

    async def runtime_activity(self, name, message):
        return await workflow.execute_activity(
            name, message, start_to_close_timeout=timedelta(seconds=5),
            schedule_to_close_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(initial_interval=timedelta(milliseconds=100),
                                    maximum_interval=timedelta(seconds=1), maximum_attempts=3),
        )

    async def bounded_run(self, argument):
        continuation = argument.get("_continuation")
        if continuation is None:
            initial = argument
            binding = await self.runtime_activity("salience.v4.fixture_runtime_binding", initial)
            deadline = min(datetime.fromisoformat(binding["deadline"]), workflow.now()+timedelta(minutes=5))
            processed = 1
            await self.consume(initial)
        else:
            if not workflow.info().continued_run_id or type(continuation.get("processed")) is not int or not 1 <= continuation["processed"] <= 32:
                raise ApplicationError("invalid fixture continuation",non_retryable=True)
            initial = continuation["initial"]
            binding = continuation["binding"]
            deadline = datetime.fromisoformat(continuation["deadline"])
            processed = continuation["processed"]
            self.pending = continuation["pending"] + self.pending
            self.overflow = continuation["overflow"] or self.overflow or len(self.pending)>32
        batch = 0
        identity = {key: binding[key] for key in ("cycle_id", "context_id", "operation_id")}
        while True:
            remaining = deadline-workflow.now()
            reason = ("held_timeout" if remaining.total_seconds() <= 0 else
                      "held_message_limit" if self.overflow or processed >= 32 else None)
            if reason is None:
                try:
                    await workflow.wait_condition(lambda: bool(self.pending) or self.overflow, timeout=remaining)
                except TimeoutError:
                    reason = "held_timeout"
            if reason is not None:
                await self.runtime_activity("salience.v4.fixture_runtime_hold", identity | {"state": reason})
                return {"cycle_id": identity["cycle_id"], "state": reason}
            if self.overflow:
                continue
            message = self.pending.pop(0)
            processed += 1
            batch += 1
            if message.get("cycle_id") == initial["cycle_id"]:
                receipt = await self.consume(message)
                if message["kind"] == "close":
                    return {"cycle_id": identity["cycle_id"], "state": receipt["state"]}
            if batch >= 8 or workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                workflow.continue_as_new({"_continuation": {
                    "initial": initial, "binding": binding, "deadline": deadline.isoformat(),
                    "processed": processed, "pending": self.pending, "overflow": self.overflow,
                }})
