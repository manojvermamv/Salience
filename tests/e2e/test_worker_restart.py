import os

import pytest

from salience.workflows.jobs import run_restart_reconciliation_scenario


@pytest.mark.asyncio
async def test_killed_worker_resumes_checkpoint_without_duplicate_effect() -> None:
    result = await run_restart_reconciliation_scenario(
        temporal_target=os.environ["TEST_TEMPORAL_TARGET"],
    )

    assert result.status == "succeeded"
    assert result.checkpoint_count >= 2
    assert result.provider_effect_calls == 1
    assert result.reconciled is True

