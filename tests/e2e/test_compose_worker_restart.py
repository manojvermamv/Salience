from pathlib import Path

import pytest

from salience.workflows.worker import run_compose_restart_scenario


@pytest.mark.asyncio
async def test_compose_worker_crash_reconciles_remote_effect_once(
    task8_candidate_state: dict[str, object] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if task8_candidate_state is None:
        pytest.skip("Task 8 exact candidate state is wholly absent")

    project_directory = Path(__file__).parents[2]
    candidate_root = Path(task8_candidate_state["candidate_root"])
    control_bundle = Path(task8_candidate_state["control_bundle"])
    monkeypatch.setenv(
        "SALIENCE_WHEEL_FILE",
        (candidate_root / "salience.whl").relative_to(project_directory).as_posix(),
    )
    monkeypatch.setenv(
        "PUBLICATION_WORKER_LAUNCHER_PATH",
        str(control_bundle / "launch-v4-publication-worker.py"),
    )
    monkeypatch.setenv(
        "PUBLICATION_WORKER_LOCK_PATH",
        str(control_bundle / "publication-worker-launch-lock.json"),
    )
    monkeypatch.setenv(
        "PUBLICATION_WORKER_LOCK_DIGEST_PATH",
        str(control_bundle / "publication-worker-launch-lock.sha256"),
    )

    result = await run_compose_restart_scenario(
        project_directory=project_directory,
    )

    assert result.status == "succeeded"
    assert result.provider_effect_calls == 1
    assert result.reconciled is True
    assert result.canonical_checkpoint_count >= 2
    assert result.canonical_effect_count == 1
