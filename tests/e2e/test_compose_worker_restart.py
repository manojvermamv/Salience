from pathlib import Path

import pytest

from salience.workflows.worker import run_compose_restart_scenario


@pytest.mark.asyncio
async def test_compose_worker_crash_reconciles_remote_effect_once() -> None:
    result = await run_compose_restart_scenario(
        project_directory=Path(__file__).parents[2],
    )

    assert result.status == "succeeded"
    assert result.provider_effect_calls == 1
    assert result.reconciled is True
    assert result.canonical_checkpoint_count >= 2
    assert result.canonical_effect_count == 1
