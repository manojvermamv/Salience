import os

import pytest

from salience.workflows.jobs import run_terminal_state_scenarios


@pytest.mark.asyncio
async def test_durable_dummy_job_records_bounded_terminal_states() -> None:
    results = await run_terminal_state_scenarios(
        temporal_target=os.environ["TEST_TEMPORAL_TARGET"],
        database_url=os.environ["TEST_DATABASE_URL"],
    )

    assert results.retry_exhausted.status == "dead_lettered"
    assert results.retry_exhausted.effect_attempts == 3
    assert results.retry_exhausted.dead_letter_count == 1

    assert results.timed_out.status == "timed_out"
    assert results.timed_out.dead_letter_count == 1

    assert results.cancelled.status == "cancelled"
    assert results.cancelled.checkpoint_count >= 1

    assert results.policy_denied.status == "denied"
    assert results.policy_denied.effect_attempts == 0
    assert results.budget_denied.status == "denied"
    assert results.budget_denied.effect_attempts == 0

    assert results.dry_run.status == "succeeded"
    assert results.dry_run.effect_attempts == 0
    assert results.dry_run.external_reference.startswith("dry-run:")
