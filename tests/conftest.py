"""Provision the pinned local dependencies for integration and e2e tests."""

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


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
