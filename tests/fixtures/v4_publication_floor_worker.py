"""Run the externally gated, wheel-installed publication fixture worker."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterator


CONTROL_LAUNCHER = "/run/salience-control/launch-v4-publication-worker.py"
CONTROL_LOCK = "/run/salience-control/publication-worker-launch-lock.json"
CONTROL_DIGEST = "/run/salience-control/publication-worker-launch-lock.sha256"
NETWORK = "salience_default"


@contextmanager
def _without_shared_checkout() -> Iterator[None]:
    """Keep the shared source checkout out of the launch helper's import path."""
    checkout = Path(__file__).resolve().parents[2]
    original_path = list(sys.path)
    original_cwd = Path.cwd()
    filtered = []
    for entry in original_path:
        candidate = Path(entry or original_cwd).resolve()
        if candidate == checkout or checkout in candidate.parents:
            continue
        filtered.append(entry)
    sys.path[:] = filtered
    os.chdir(tempfile.gettempdir())
    try:
        yield
    finally:
        sys.path[:] = original_path
        os.chdir(original_cwd)


def _docker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    for key in tuple(environment):
        if key.startswith("SALIENCE_PUBLICATION_"):
            environment.pop(key)
    return environment


def _mount(source: Path, target: str) -> str:
    return f"type=bind,source={source.resolve()},target={target},readonly"


def image_id(image: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        check=True,
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
        env=_docker_environment(),
    )
    return result.stdout.strip()


def start_rollback_worker(
    *,
    image: str,
    database_url: str,
    temporal_target: str,
    workspace_id: str,
    expired_operation_id: str,
    cancel_operation_id: str,
    task_queue: str,
    control_bundle: Path,
    name: str,
) -> dict[str, str]:
    """Start the real Compose-built image through its fixed external launcher."""
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        NETWORK,
        "--env",
        f"DATABASE_URL={database_url}",
        "--env",
        f"TEST_DATABASE_URL={database_url}",
        "--env",
        f"TEST_TEMPORAL_TARGET={temporal_target}",
        "--env",
        f"V4_LEGACY_FIXTURE_QUEUE={task_queue}",
        "--env",
        f"V4_FIXTURE_WORKSPACE={workspace_id}",
        "--env",
        "SALIENCE_DEPLOYMENT_MODE=fixture",
        "--env",
        "SALIENCE_EFFECTS_ENABLED=false",
        "--mount",
        _mount(control_bundle / "launch-v4-publication-worker.py", CONTROL_LAUNCHER),
        "--mount",
        _mount(control_bundle / "publication-worker-launch-lock.json", CONTROL_LOCK),
        "--mount",
        _mount(control_bundle / "publication-worker-launch-lock.sha256", CONTROL_DIGEST),
        image,
        "/usr/local/bin/python",
        CONTROL_LAUNCHER,
        "--mode",
        "rollback-restart",
        "--worker-kind",
        "legacy-fixture",
        "--workspace-id",
        workspace_id,
        "--expired-hold-operation-id",
        expired_operation_id,
        "--cancel-hold-operation-id",
        cancel_operation_id,
    ]
    with _without_shared_checkout():
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_docker_environment(),
            timeout=30,
        )
    return {
        "container_id": result.stdout.strip(),
        "container_name": name,
        "image": image,
        "image_id": image_id(image),
        "launcher_argv": json.dumps(
            command[-12:], separators=(",", ":")
        ),
    }


def inspect_worker(name: str) -> dict[str, object]:
    with _without_shared_checkout():
        result = subprocess.run(
            ["docker", "inspect", name],
            check=True,
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_docker_environment(),
        )
    inspected = json.loads(result.stdout)[0]
    environment = {}
    for item in inspected["Config"].get("Env", []):
        key, _, value = item.partition("=")
        if key in {
            "SALIENCE_DEPLOYMENT_MODE",
            "SALIENCE_EFFECTS_ENABLED",
            "TEST_TEMPORAL_TARGET",
            "V4_LEGACY_FIXTURE_QUEUE",
            "V4_FIXTURE_WORKSPACE",
        }:
            environment[key] = value
    return {
        "image_id": inspected["Image"],
        "path": inspected["Path"],
        "args": inspected["Args"],
        "state": inspected["State"]["Status"],
        "environment": environment,
        "mounts": [
            {
                "source": item.get("Source"),
                "destination": item.get("Destination"),
                "read_write": item.get("RW"),
            }
            for item in inspected.get("Mounts", [])
        ],
    }


def stop_worker(name: str) -> dict[str, object]:
    """Stop a worker after the fixture histories settle and retain its logs."""
    with _without_shared_checkout():
        before = subprocess.run(
            ["docker", "inspect", name],
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_docker_environment(),
        )
        if before.returncode != 0:
            return {"container_name": name, "state": "already-removed", "logs": ""}
        inspected = json.loads(before.stdout)[0]
        running = bool(inspected["State"]["Running"])
        if running:
            subprocess.run(
                ["docker", "stop", "--time", "5", name],
                check=True,
                capture_output=True,
                text=True,
                cwd=tempfile.gettempdir(),
                env=_docker_environment(),
                timeout=20,
            )
        logs = subprocess.run(
            ["docker", "logs", name],
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_docker_environment(),
            timeout=15,
        )
        after = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", name],
            check=True,
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_docker_environment(),
        )
        subprocess.run(
            ["docker", "rm", name],
            check=True,
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_docker_environment(),
            timeout=15,
        )
    return {
        "container_name": name,
        "state": after.stdout.strip(),
        "logs": logs.stdout + logs.stderr,
        "logs_exit_code": logs.returncode,
    }


def invoke_launcher(
    *,
    image: str,
    database_url: str,
    control_bundle: Path,
    candidate_lock: Path | None = None,
    independent_digest: Path | None = None,
    extra_mounts: tuple[tuple[Path, str], ...] = (),
    argv: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    """Run the external launcher as a short-lived denial probe."""
    lock_path = candidate_lock or control_bundle / "publication-worker-launch-lock.json"
    digest_path = independent_digest or control_bundle / "publication-worker-launch-lock.sha256"
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        NETWORK,
        "--env",
        f"DATABASE_URL={database_url}",
        "--mount",
        _mount(control_bundle / "launch-v4-publication-worker.py", CONTROL_LAUNCHER),
        "--mount",
        _mount(lock_path, CONTROL_LOCK),
        "--mount",
        _mount(digest_path, CONTROL_DIGEST),
    ]
    for source, target in extra_mounts:
        command.extend(("--mount", _mount(source, target)))
    command.extend((image, "/usr/local/bin/python", CONTROL_LAUNCHER, *argv))
    with _without_shared_checkout():
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_docker_environment(),
            timeout=30,
            check=False,
        )
