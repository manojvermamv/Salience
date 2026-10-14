"""Provision the pinned local dependencies for integration and e2e tests."""

import os
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def task8_candidate_state():
    """Validate the exact current candidate before any test-owned DB setup."""
    helper_path = ROOT / "tests/fixtures/v4_publication_candidate_prerequisites.py"
    spec = importlib.util.spec_from_file_location(
        "v4_publication_candidate_prerequisites", helper_path
    )
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    candidate = helper.load_candidate_state(
        candidate_root=ROOT / "artifacts/v4-next-p1/agents/p1-publication-recovery-0043",
        tracked_lock_path=ROOT / "deployment/v4/publication-worker-launch-lock.json",
        tracked_launcher_path=ROOT / "scripts/launch-v4-publication-worker.py",
        runtime_prefix=Path("/tmp/p1-publication-recovery-0043/runtime-prefix"),
        expected_application_commit=subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
    )
    if candidate is None:
        pytest.skip("Task 8 exact candidate state is wholly absent")
    return candidate


@pytest.fixture
def task8_candidate_database(task8_candidate_state, request):
    """Set up a candidate test database only after the exact image gate passes."""
    return request.getfixturevalue("active_0042_database")


def _service_ip(service: str) -> str:
    container_id = subprocess.run(
        ["docker", "compose", "ps", "-q", service],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not container_id:
        raise RuntimeError(f"{service} did not start")
    return subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            container_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def pytest_sessionstart() -> None:
    if os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_TEMPORAL_TARGET"):
        return
    subprocess.run(
        ["docker", "compose", "up", "-d", "postgres", "temporal"],
        cwd=ROOT,
        check=True,
    )
    postgres_ip = _service_ip("postgres")
    temporal_ip = _service_ip("temporal")
    database_url = f"postgresql+asyncpg://salience:salience@{postgres_ip}:5432/salience"
    environment = os.environ | {"DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    os.environ["TEST_DATABASE_URL"] = database_url.replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    os.environ["TEST_TEMPORAL_TARGET"] = f"{temporal_ip}:7233"
