"""Real worker hard-exit injector, available only to disposable fixtures."""

import asyncio
import os

from temporalio.client import Client

from salience.cycles.legacy_dispatch import require_fixture
from salience.cycles.legacy_runtime import main
from salience.workflows.intelligence import IntelligenceLoopWorkflow

def install_crash_after_start_hook() -> None:
    """Install the hard-exit hook only for explicit no-effects fixtures."""
    require_fixture()
    if os.environ.get("LEGACY_FIXTURE_CRASH_AFTER_START") != "1":
        return
    if os.environ.get("SALIENCE_EFFECTS_ENABLED") != "false":
        raise PermissionError("legacy dispatch crash injection requires effects to be disabled")
    original = Client.start_workflow

    async def crash_after_start(self, workflow, *args, **kwargs):
        result = await original(self, workflow, *args, **kwargs)
        if workflow == IntelligenceLoopWorkflow.run:
            os._exit(137)
        return result

    Client.start_workflow = crash_after_start


if __name__ == "__main__":
    install_crash_after_start_hook()
    asyncio.run(main())
