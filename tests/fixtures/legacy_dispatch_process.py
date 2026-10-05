"""Real worker hard-exit injector, available only to disposable fixtures."""

import asyncio
import os

from temporalio.client import Client

from salience.cycles.legacy_dispatch import require_fixture
from salience.cycles.legacy_runtime import main
from salience.workflows.intelligence import IntelligenceLoopWorkflow

require_fixture()
original = Client.start_workflow


async def crash_after_start(self, workflow, *args, **kwargs):
    result = await original(self, workflow, *args, **kwargs)
    if workflow == IntelligenceLoopWorkflow.run:
        os._exit(137)
    return result


if os.environ.get("LEGACY_FIXTURE_CRASH_AFTER_START") == "1":
    Client.start_workflow = crash_after_start

asyncio.run(main())
