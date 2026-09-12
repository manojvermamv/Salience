import os

import pytest

from salience.verification import run_cross_phase_verifier


@pytest.mark.asyncio
async def test_clean_stack_executes_phases_one_through_four() -> None:
    report = await run_cross_phase_verifier(
        temporal_target=os.environ["TEST_TEMPORAL_TARGET"],
        database_url=os.environ["TEST_DATABASE_URL"],
    )

    assert report.phase_1_recovery
    assert report.phase_2_agents
    assert report.phase_3_protocols
    assert report.phase_4_bootstrap
    assert report.retained_ids
