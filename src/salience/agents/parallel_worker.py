"""Explicit no-effects worker; durable claims can be shared across processes."""
import asyncio
import os
from uuid import UUID

from salience.agents.execution import AgentInvocation
from salience.agents.parallel_store import ParallelTeamStore, parent_context
from salience.observability.tracing import TraceContext


class ParallelTeamWorker:
    def __init__(self, store, *, slots=8, poll_seconds=0.05):
        if type(slots) is not int or not 1 <= slots <= 8:
            raise ValueError("worker slots must be between 1 and 8")
        if not 0.01 <= poll_seconds <= 1:
            raise ValueError("bounded poll interval required")
        self.store = store
        self.slots = slots
        self.poll_seconds = poll_seconds

    async def execute(self, task):
        try:
            if not await asyncio.to_thread(self.store.active, task):
                await asyncio.to_thread(self.store.finish, task, state='unknown', error='AuthorityChanged')
                return
            async with asyncio.timeout(task['timeout_seconds']):
                result = await self.store.service.invoke_child(
                    AgentInvocation(task['agent_manifest']['agent_id'], task['input_payload'], mode='async'),
                    parent_context(task['parent_context']), run_id=task['id'],
                    trace_context=TraceContext.from_carrier(task['execution_context']['trace']),
                )
            state, output, error = 'succeeded', result.output, None
        except asyncio.CancelledError:
            # A cancelled runtime may already have sent a request. Never replay it.
            await asyncio.shield(asyncio.to_thread(self.store.finish, task, state='unknown', error='Interrupted'))
            raise
        except TimeoutError:
            state, output, error = 'unknown', None, 'DeadlineExceeded'
        except Exception as failure:
            state, output, error = 'failed', None, type(failure).__name__
        await asyncio.to_thread(self.store.finish, task, state=state, output=output, error=error)

    async def run(self, stop):
        active = {}
        try:
            while not stop.is_set():
                for future, task in list(active.items()):
                    if future.done():
                        if not future.cancelled():
                            future.result()  # Persistence failures stop the worker, leaving claims durable.
                        del active[future]
                    elif not future.cancelling() and not await asyncio.to_thread(self.store.active, task):
                        future.cancel()
                while len(active) < self.slots and not stop.is_set():
                    # Shield admission: cancellation must not lose a committed claim.
                    claim = asyncio.create_task(asyncio.to_thread(self.store.claim))
                    try:
                        task = await asyncio.shield(claim)
                    except asyncio.CancelledError:
                        task = await claim
                        if task:
                            await asyncio.to_thread(self.store.finish, task, state='unknown', error='Interrupted')
                        raise
                    if task is None:
                        break
                    active[asyncio.create_task(self.execute(task))] = task
                await asyncio.sleep(self.poll_seconds)
        finally:
            for future in active:
                if not future.cancelling():
                    future.cancel()
            results = await asyncio.gather(*active, return_exceptions=True)
            for task in active.values():
                await asyncio.to_thread(self.store.finish, task, state='unknown', error='Interrupted')
            for result in results:
                if isinstance(result, Exception):
                    raise result


def main():
    if os.environ.get('SALIENCE_DEPLOYMENT_MODE') != 'fixture' or os.environ.get('SALIENCE_EFFECTS_ENABLED', 'false') != 'false':
        raise ValueError('parallel worker requires explicit no-effects fixture mode')
    from salience.agents.fixtures import fixture_agent_service

    store = ParallelTeamStore(os.environ['DATABASE_URL'], workspace_id=UUID(os.environ['SALIENCE_WORKSPACE_ID']), service=fixture_agent_service())
    asyncio.run(ParallelTeamWorker(store).run(asyncio.Event()))


if __name__ == '__main__':
    main()
