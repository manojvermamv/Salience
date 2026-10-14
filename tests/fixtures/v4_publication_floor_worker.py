"""Run the externally gated, wheel-installed publication fixture worker."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import ipaddress
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Iterator
from urllib.parse import urlsplit
from uuid import UUID, uuid4


CONTROL_LAUNCHER = "/run/salience-control/launch-v4-publication-worker.py"
CONTROL_LOCK = "/run/salience-control/publication-worker-launch-lock.json"
CONTROL_DIGEST = "/run/salience-control/publication-worker-launch-lock.sha256"
ADAPTER_RECORDER_FIXTURE = "/run/task7-adapter-operation-recorder.py"
NETWORK = "salience_default"
TASK8_RECORDER_NAME_PREFIX = "task8-adapter-recorder-"
TASK8_RECORDER_PROJECT = "salience-p1-publication-recovery-0043"
TASK8_RECORDER_COMMAND = (
"-I",
"-B",
"-S",
    "/run/v4-publication-adapter-operation-receiver.py",
    "--host",
    "0.0.0.0",
    "--port",
    "8081",
)
QUALIFICATION_PROGRESS_FIELDS = frozenset(
    {
        "schema_version",
        "application_commit",
        "image_ref",
        "image_id",
        "launch_lock_sha256",
        "launcher_sha256",
        "interpreter",
        "interpreter_sha256",
        "wheel_sha256",
        "record_sha256",
        "source_manifest_sha256",
        "source_files",
        "runtime_imports",
        "runtime_root",
        "runtime_manifest_sha256",
        "source_tree_loaded",
        "effects_enabled",
        "production_effects_enabled",
        "fixture_publication_budget",
        "application_ancestry",
        "workspace_id",
        "operation_ids",
        "hold_ids",
        "rollback_launch_fixture",
        "legacy_null_v1_fixture",
    }
)
_ROLLBACK_FIXTURE_FIELDS = frozenset(
    {
        "schema_version",
        "application_commit",
        "image_ref",
        "image_id",
        "launch_lock_sha256",
        "disposable_targets",
        "workspace_id",
        "operation_ids",
        "hold_ids",
        "history_fixture",
        "liability_snapshot_hashes",
        "hold_row_hashes",
    }
)
_LEGACY_NULL_FIXTURE_FIELDS = frozenset(
    {
        "schema_version",
        "application_commit",
        "image_ref",
        "image_id",
        "launch_lock_sha256",
        "disposable_targets",
        "workspace_id",
        "operation_ids",
        "hold_ids",
        "execution_mode",
        "liability_snapshot_hashes",
        "hold_row_hashes",
        "legacy_null_operation_id",
    }
)
_REASONS = {"expired_permit_receipt", "published_during_cancel"}
_RUNTIME_MANIFEST_SCRIPT = r"""
import hashlib, json, os, stat, sys
from pathlib import Path
root = Path(sys.argv[1])
files = {}
pyc_files = []
for current, directories, names in os.walk(root, followlinks=False):
    for name in (*directories, *names):
        path = Path(current) / name
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = path.resolve(strict=False)
            files[relative] = "symlink:" + os.readlink(path)
            if path.name.endswith(".pyc") or target.suffix == ".pyc":
                pyc_files.append(relative)
        elif path.is_dir():
            files[relative] = "directory"
        else:
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                files[relative] = "non-regular"
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                files[relative] = "sha256:" + digest
            if path.name.endswith(".pyc"):
                pyc_files.append(relative)
print(json.dumps({"files": files, "pyc_files": sorted(pyc_files)}, sort_keys=True, separators=(",", ":")))
"""


def is_task8_adapter_recorder_container(
    container: dict[str, object], *, expected_image_id: str
) -> bool:
    """Recognize only the Task 8 receiver, never a candidate-image worker."""
    name = str(container.get("Name", "")).lstrip("/")
    config = container.get("Config")
    if not isinstance(config, dict):
        return False
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        return False
    return (
        container.get("Image") == expected_image_id
        and name.startswith(TASK8_RECORDER_NAME_PREFIX)
        and bool(name.removeprefix(TASK8_RECORDER_NAME_PREFIX))
        and labels.get("com.docker.compose.project") == TASK8_RECORDER_PROJECT
        and labels.get("com.docker.compose.service") == "worker"
        and config.get("Entrypoint") == ["/usr/local/bin/python"]
        and config.get("Cmd") == list(TASK8_RECORDER_COMMAND)
    )
_ADAPTER_RECORDER_WORKER_BOOTSTRAP = r"""import importlib.util, os, sys
runtime_root = sys.argv[1]
sys.path.insert(0, runtime_root)
fixture_path = os.environ["TASK7_ADAPTER_RECORDER_FIXTURE"]
spec = importlib.util.spec_from_file_location(
    "task7_adapter_operation_recorder", fixture_path
)
if spec is None or spec.loader is None:
    raise RuntimeError("adapter-operation recorder fixture cannot be loaded")
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)
recorder.install_publication_activities_adapter_recorder(
    receiver_url=os.environ["TASK7_ADAPTER_RECORDER_URL"],
    run_id=os.environ["TASK7_ADAPTER_RECORDER_RUN_ID"],
    worker_id=os.environ["TASK7_ADAPTER_RECORDER_WORKER_ID"],
)
"""
_ADAPTER_RECORDER_LAUNCH_SHIM = f"""import os, runpy, sys
launcher_path, *launcher_args = sys.argv[1:]
original_execve = os.execve
worker_bootstrap_prefix = {json.dumps(_ADAPTER_RECORDER_WORKER_BOOTSTRAP)}
def execve_with_adapter_recorder(path, argv, environment):
    argv = list(argv)
    if (
        len(argv) > 5
        and argv[1:5] == ["-I", "-B", "-S", "-c"]
        and "runtime_root, module, *module_args = sys.argv[1:]" in argv[5]
    ):
        argv[5] = worker_bootstrap_prefix + argv[5]
    return original_execve(path, argv, environment)
os.execve = execve_with_adapter_recorder
sys.argv = [launcher_path, *launcher_args]
runpy.run_path(launcher_path, run_name="__main__")
"""


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


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_fixture_target(target: object) -> None:
    assert isinstance(target, dict)
    assert set(target) == {
        "database_name",
        "database_host",
        "database_port",
        "temporal_target",
        "adapter_recorder",
        "fixture_lifetime",
    }
    assert isinstance(target["database_name"], str)
    assert re.fullmatch(r"recovery_floor_(?:active|legacy)_[0-9a-f]{12}", target["database_name"])
    def local_host(host: str) -> bool:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return host in {"localhost", "postgres", "temporal", "adapter-recorder"}
        return address.is_private or address.is_loopback

    assert isinstance(target["database_host"], str) and local_host(target["database_host"])
    assert isinstance(target["database_port"], int) and target["database_port"] > 0
    temporal = urlsplit("//" + target["temporal_target"])
    assert temporal.hostname and local_host(temporal.hostname)
    assert temporal.port and temporal.username is None and temporal.password is None
    assert not temporal.path and not temporal.query and not temporal.fragment
    assert target["fixture_lifetime"] == "task8-persistent", (
        "fixture target must persist through qualification, not be function-scoped"
    )
    recorder = target["adapter_recorder"]
    assert isinstance(recorder, dict)
    assert set(recorder) == {"base_url", "run_id"}
    recorder_url = urlsplit(recorder["base_url"])
    assert recorder_url.scheme == "http"
    assert recorder_url.hostname and local_host(recorder_url.hostname)
    assert recorder_url.username is None and recorder_url.password is None
    assert recorder_url.path in {"", "/"}
    assert not recorder_url.query and not recorder_url.fragment
    assert str(UUID(recorder["run_id"])) == recorder["run_id"]


def _validate_history_fixture(history_fixture: object, operation_ids: dict[str, str]) -> None:
    assert isinstance(history_fixture, dict) and set(history_fixture) == _REASONS
    for reason, operation_id in operation_ids.items():
        history = history_fixture[reason]
        assert set(history) == {"workflow_id", "task_queue", "last_event_id", "history_sha256"}
        assert history["workflow_id"] == "salience-v4-legacy-operation:" + operation_id
        assert isinstance(history["task_queue"], str) and history["task_queue"]
        assert isinstance(history["last_event_id"], int) and history["last_event_id"] > 0
        assert len(history["history_sha256"]) == 64
        assert all(character in "0123456789abcdef" for character in history["history_sha256"])
        assert str(UUID(operation_id)) == operation_id


def _validate_state_hashes(fixture: dict[str, object], database_state: object) -> None:
    assert isinstance(database_state, dict)
    assert set(database_state) in (
        {"head", "workspace_id", "holds", "liabilities"},
        {"head", "workspace_id", "holds", "liabilities", "legacy_null_v1"},
    ), "database state did not read back the complete 0043 hold fixture"
    assert database_state["head"] == "0043_complete_publication_recovery_liabilities", (
        "database state did not read back migration head 0043"
    )
    assert database_state["workspace_id"] == fixture["workspace_id"]
    holds = database_state["holds"]
    liabilities = database_state["liabilities"]
    operation_ids = fixture["operation_ids"]
    hold_ids = fixture["hold_ids"]
    assert isinstance(holds, dict) and set(holds) == _REASONS
    assert isinstance(liabilities, dict) and set(liabilities) == _REASONS
    expected_hold_hashes = {}
    expected_liability_hashes = {}
    for reason in _REASONS:
        hold = holds[reason]
        assert hold["id"] == hold_ids[reason], f"hold ID readback is stale for {reason}"
        assert hold["operation_id"] == operation_ids[reason], (
            f"hold operation readback is stale for {reason}"
        )
        assert hold["workspace_id"] == fixture["workspace_id"], (
            f"hold workspace readback is stale for {reason}"
        )
        assert hold["reason"] == reason, f"hold reason readback is stale for {reason}"
        expected_hold_hashes[reason] = _canonical_json_sha256(hold)
        expected_liability_hashes[reason] = _canonical_json_sha256(liabilities[reason])
    assert fixture["hold_row_hashes"] == expected_hold_hashes
    assert fixture["liability_snapshot_hashes"] == expected_liability_hashes


async def write_qualification_progress(
    candidate_root: Path,
    *,
    rollback_launch_fixture: dict[str, object],
    legacy_null_v1_fixture: dict[str, object],
    persistent_fixture: bool,
    read_database_state,
    read_legacy_database_state,
    read_histories,
    read_recorder_events,
    image_inspector=None,
    runtime_manifest_reader=None,
) -> dict[str, object]:
    """Publish exact Task 8 progress only after independent fixture readbacks.

    Callers provide readers that connect to the live disposable PostgreSQL,
    Temporal, and recorder targets. The producer compares those observations to
    the intended fixture before atomically replacing the progress file.
    """
    assert persistent_fixture is True, "Task 8 fixture targets must persist through qualification"
    image_inspector = image_id if image_inspector is None else image_inspector
    runtime_manifest_reader = (
        image_runtime_manifest
        if runtime_manifest_reader is None
        else runtime_manifest_reader
    )
    candidate_root = Path(candidate_root)
    assert candidate_root.is_dir() and not candidate_root.is_symlink()
    control_bundle = candidate_root / "control-bundle"
    assert control_bundle.is_dir() and not control_bundle.is_symlink()
    expected_control_files = {
        "launch-v4-publication-worker.py",
        "publication-worker-launch-lock.json",
        "publication-worker-launch-lock.sha256",
    }
    assert {path.name for path in control_bundle.iterdir()} == expected_control_files

    def regular_bytes(path: Path) -> bytes:
        metadata = path.lstat()
        assert not path.is_symlink() and metadata.st_mode & 0o170000 == 0o100000
        return path.read_bytes()

    launcher_bytes = regular_bytes(control_bundle / "launch-v4-publication-worker.py")
    lock_bytes = regular_bytes(control_bundle / "publication-worker-launch-lock.json")
    lock = json.loads(lock_bytes)
    assert lock_bytes == _canonical_json_bytes(lock)
    lock_digest = _sha256(lock_bytes)
    assert regular_bytes(control_bundle / "publication-worker-launch-lock.sha256") == (
        lock_digest + "\n"
    ).encode("ascii")
    assert lock["launcher_sha256"] == _sha256(launcher_bytes)
    pin = lock["pin"]
    assert re.fullmatch(r"[0-9a-f]{40}", pin["application_commit"])
    assert re.fullmatch(r"[0-9a-f]{40}", pin["eligible_anchor"])
    assert re.fullmatch(r"[0-9a-f]{64}", pin["artifact_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", pin["distribution_record_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", pin["source_manifest_sha256"])
    attestation_path = candidate_root / "image-attestation.json"
    attestation = json.loads(regular_bytes(attestation_path))
    image_ref = attestation["image_ref"]
    observed_image_id = attestation["image_id"]
    assert image_ref == f"salience-p1-floor-{pin['application_commit'][:7]}-worker:latest"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", observed_image_id)
    assert attestation["application_commit"] == pin["application_commit"]
    assert attestation["wheel_sha256"] == pin["artifact_sha256"]
    assert attestation["record_sha256"] == pin["distribution_record_sha256"]
    assert attestation["record_ok"] is True
    assert attestation["pyc_files"] == []
    assert attestation["source_tree_loaded"] is False
    assert image_inspector(image_ref) == observed_image_id
    image_id_path = candidate_root / "worker-image-id.txt"
    assert regular_bytes(image_id_path).decode("ascii").strip() == observed_image_id
    manifest = runtime_manifest_reader(observed_image_id, lock["runtime_root"])
    assert isinstance(manifest, dict)
    assert set(manifest) == {"files", "pyc_files"}
    assert isinstance(manifest["files"], dict) and manifest["files"]
    assert manifest["pyc_files"] == []
    runtime_manifest_digest = _canonical_json_sha256(manifest)
    assert attestation.get("runtime_manifest_sha256") == runtime_manifest_digest

    for fixture, expected_fields in (
        (rollback_launch_fixture, _ROLLBACK_FIXTURE_FIELDS),
        (legacy_null_v1_fixture, _LEGACY_NULL_FIXTURE_FIELDS),
    ):
        assert set(fixture) == expected_fields
        assert fixture["schema_version"] == (
            "salience.publication-floor-rollback-launch-fixture.v1"
            if fixture is rollback_launch_fixture
            else "salience.publication-floor-legacy-null-v1-fixture.v1"
        )
        assert fixture["application_commit"] == pin["application_commit"]
        assert fixture["image_ref"] == image_ref
        assert fixture["image_id"] == observed_image_id
        assert fixture["launch_lock_sha256"] == lock_digest
        assert str(UUID(fixture["workspace_id"])) == fixture["workspace_id"]
        assert set(fixture["operation_ids"]) == _REASONS
        assert set(fixture["hold_ids"]) == _REASONS
        assert len(set(fixture["operation_ids"].values())) == 2
        assert len(set(fixture["hold_ids"].values())) == 2
        assert all(str(UUID(value)) == value for value in fixture["hold_ids"].values())
        assert fixture["operation_ids"] == {
            reason: str(UUID(value)) for reason, value in fixture["operation_ids"].items()
        }
        if fixture is rollback_launch_fixture:
            _validate_history_fixture(fixture["history_fixture"], fixture["operation_ids"])
        else:
            assert fixture["execution_mode"] == {
                "mode": "preflight_only",
                "denial_stage": "before_worker_registration",
                "worker_started": False,
                "outbox_dispatched": False,
            }
        _require_fixture_target(fixture["disposable_targets"])

    assert rollback_launch_fixture["disposable_targets"]["database_name"] != (
        legacy_null_v1_fixture["disposable_targets"]["database_name"]
    ), "legacy NULL-v1 state must be isolated in a separate persistent database"
    assert rollback_launch_fixture["disposable_targets"]["temporal_target"] == (
        legacy_null_v1_fixture["disposable_targets"]["temporal_target"]
    )
    assert (
        legacy_null_v1_fixture["legacy_null_operation_id"]
        == legacy_null_v1_fixture["operation_ids"]["published_during_cancel"]
    )
    assert legacy_null_v1_fixture["operation_ids"]["published_during_cancel"] != (
        rollback_launch_fixture["operation_ids"]["published_during_cancel"]
    ), "legacy NULL-v1 denial must use a distinct persisted hold row"

    ordinary_state = read_database_state()
    _validate_state_hashes(rollback_launch_fixture, ordinary_state)
    assert all(
        hold.get("liability_snapshot_version") == "salience.publication-liability.v1"
        for hold in ordinary_state["holds"].values()
    ), "both ordinary Task 8 holds must contain v1 liability snapshots"
    assert ordinary_state == read_database_state(), "Task 8 database readback changed during publication"
    legacy_state = read_legacy_database_state()
    _validate_state_hashes(legacy_null_v1_fixture, legacy_state)
    null_row = legacy_state.get("legacy_null_v1")
    assert isinstance(null_row, dict)
    assert set(null_row) == {"operation_id", "fields"}
    assert null_row["operation_id"] == legacy_null_v1_fixture["legacy_null_operation_id"]
    assert null_row["fields"] == [None, None, None]
    assert legacy_state["holds"]["expired_permit_receipt"].get(
        "liability_snapshot_version"
    ) == "salience.publication-liability.v1"
    assert legacy_state["holds"]["published_during_cancel"].get(
        "liability_snapshot_version"
    ) is None
    assert legacy_state == read_legacy_database_state(), (
        "legacy NULL-v1 database readback changed during publication"
    )
    ordinary_history_readback = read_histories(
        rollback_launch_fixture["disposable_targets"]["temporal_target"],
        rollback_launch_fixture["history_fixture"],
    )
    if inspect.isawaitable(ordinary_history_readback):
        ordinary_history_readback = await ordinary_history_readback
    assert ordinary_history_readback == rollback_launch_fixture["history_fixture"]
    recorder = rollback_launch_fixture["disposable_targets"]["adapter_recorder"]
    assert read_recorder_events(recorder["base_url"], recorder["run_id"]) == []

    progress = {
        "schema_version": "salience.publication-floor-qualification.v1",
        "application_commit": pin["application_commit"],
        "image_ref": image_ref,
        "image_id": observed_image_id,
        "launch_lock_sha256": lock_digest,
        "launcher_sha256": lock["launcher_sha256"],
        "interpreter": lock["interpreter_path"],
        "interpreter_sha256": lock["interpreter_sha256"],
        "wheel_sha256": pin["artifact_sha256"],
        "record_sha256": pin["distribution_record_sha256"],
        "source_manifest_sha256": pin["source_manifest_sha256"],
        "source_files": pin["source_files"],
        "runtime_imports": pin["runtime_imports"],
        "runtime_root": lock["runtime_root"],
        "runtime_manifest_sha256": runtime_manifest_digest,
        "source_tree_loaded": False,
        "effects_enabled": False,
        "production_effects_enabled": False,
        "fixture_publication_budget": 0,
        "application_ancestry": {
            "anchor": pin["eligible_anchor"],
            "candidate": pin["application_commit"],
            "is_ancestor": True,
        },
        "workspace_id": rollback_launch_fixture["workspace_id"],
        "operation_ids": rollback_launch_fixture["operation_ids"],
        "hold_ids": rollback_launch_fixture["hold_ids"],
        "rollback_launch_fixture": rollback_launch_fixture,
        "legacy_null_v1_fixture": legacy_null_v1_fixture,
    }
    assert set(progress) == QUALIFICATION_PROGRESS_FIELDS
    progress_path = candidate_root / "worker-qualification-progress.json"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=candidate_root,
            prefix=".worker-qualification-progress-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(progress, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, progress_path)
        directory_fd = os.open(candidate_root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    written = json.loads(regular_bytes(progress_path))
    assert set(written) == QUALIFICATION_PROGRESS_FIELDS
    assert written == progress
    assert written["runtime_manifest_sha256"] == runtime_manifest_digest
    return written


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


def image_runtime_manifest(image: str, runtime_root: str) -> dict[str, object]:
    """Hash the installed runtime from an isolated, read-only image process."""
    command = [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--network",
        "none",
        "--entrypoint",
        "/usr/local/bin/python",
        image,
        "-I",
        "-B",
        "-S",
        "-c",
        _RUNTIME_MANIFEST_SCRIPT,
        runtime_root,
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
    return json.loads(result.stdout)


def container_runtime_manifest(name: str, runtime_root: str) -> dict[str, object]:
    """Hash the installed runtime inside the running read-only worker container."""
    command = [
        "docker",
        "exec",
        name,
        "/usr/local/bin/python",
        "-I",
        "-B",
        "-S",
        "-c",
        _RUNTIME_MANIFEST_SCRIPT,
        runtime_root,
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
    return json.loads(result.stdout)


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
    adapter_recorder_url: str | None = None,
    adapter_recorder_run_id: str | None = None,
    adapter_recorder_worker_id: str | None = None,
) -> dict[str, str]:
    """Start the real image through its authenticated external launcher.

    When an operation receiver is provided, a test-only bootstrap wraps the
    runtime's actual PublicationActivities adapters after launcher verification
    and before worker setup. It does not modify the authenticated runtime files.
    """
    recorder_target = (
        adapter_recorder_url is not None,
        adapter_recorder_run_id is not None,
    )
    if any(recorder_target) and not all(recorder_target):
        raise ValueError("adapter recorder URL and run ID must be provided together")

    recorder_fixture = None
    if all(recorder_target):
        recorder_parts = urlsplit(adapter_recorder_url)
        if (
            recorder_parts.scheme != "http"
            or not recorder_parts.hostname
            or recorder_parts.username is not None
            or recorder_parts.password is not None
            or recorder_parts.path not in {"", "/"}
            or recorder_parts.query
            or recorder_parts.fragment
        ):
            raise ValueError("adapter recorder target must be a local HTTP receiver URL")
        if str(UUID(adapter_recorder_run_id)) != adapter_recorder_run_id:
            raise ValueError("adapter recorder run ID must be a canonical UUID")
        if adapter_recorder_worker_id is None:
            adapter_recorder_worker_id = str(uuid4())
        if str(UUID(adapter_recorder_worker_id)) != adapter_recorder_worker_id:
            raise ValueError("adapter recorder worker ID must be a canonical UUID")
        recorder_fixture = Path(__file__).with_name(
            "v4_publication_adapter_operation_recorder.py"
        )
        metadata = recorder_fixture.lstat()
        if (
            recorder_fixture.is_symlink()
            or metadata.st_mode & 0o170000 != 0o100000
        ):
            raise ValueError("adapter-operation recorder fixture must be a regular file")

    command = [
        "docker",
        "run",
        "--detach",
        "--read-only",
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
    ]
    if recorder_fixture is not None:
        command.extend(
            (
                "--env",
                f"TASK7_ADAPTER_RECORDER_URL={adapter_recorder_url.rstrip('/')}",
                "--env",
                f"TASK7_ADAPTER_RECORDER_RUN_ID={adapter_recorder_run_id}",
                "--env",
                f"TASK7_ADAPTER_RECORDER_WORKER_ID={adapter_recorder_worker_id}",
                "--env",
                f"TASK7_ADAPTER_RECORDER_FIXTURE={ADAPTER_RECORDER_FIXTURE}",
                "--mount",
                _mount(recorder_fixture, ADAPTER_RECORDER_FIXTURE),
            )
        )

    launcher_argv = [
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
    if recorder_fixture is None:
        container_argv = [
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            CONTROL_LAUNCHER,
            *launcher_argv,
        ]
    else:
        container_argv = [
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            "-c",
            _ADAPTER_RECORDER_LAUNCH_SHIM,
            CONTROL_LAUNCHER,
            *launcher_argv,
        ]
    command.extend((image, *container_argv))
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
        "launcher_argv": json.dumps(container_argv, separators=(",", ":")),
    }


def run_adapter_operation_positive_control(
    container_name: str,
    *,
    recorder_fixture: Path,
    receiver_url: str,
    run_id: str,
) -> subprocess.CompletedProcess[str]:
    """Run the neutral protocol control inside an already-started worker container.

    The recorder source is sent over stdin to a short-lived isolated Python
    process. It is not mounted into or written under the authenticated runtime.
    Operation evidence is read back separately from the receiver service.
    """
    recorder_fixture = Path(recorder_fixture)
    metadata = recorder_fixture.lstat()
    if recorder_fixture.is_symlink() or metadata.st_mode & 0o170000 != 0o100000:
        raise ValueError("adapter-operation recorder fixture must be a regular file")
    fixture_source = recorder_fixture.read_text(encoding="utf-8")
    runner = (
        "import asyncio, sys\n"
        "source = sys.stdin.read()\n"
        "namespace = {'__name__': 'v4_publication_adapter_operation_recorder', "
        "'__file__': '<adapter-operation-recorder-fixture>'}\n"
        "exec(compile(source, namespace['__file__'], 'exec'), namespace)\n"
        "asyncio.run(namespace['run_positive_control']("
        "base_url=sys.argv[1], run_id=sys.argv[2]))\n"
    )
    command = [
        "docker",
        "exec",
        "-i",
        container_name,
        "/usr/local/bin/python",
        "-I",
        "-B",
        "-c",
        runner,
        receiver_url,
        run_id,
    ]
    with _without_shared_checkout():
        return subprocess.run(
            command,
            input=fixture_source,
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_docker_environment(),
            timeout=30,
            check=False,
        )


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
        "read_only_rootfs": bool(inspected["HostConfig"].get("ReadonlyRootfs")),
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
        "--read-only",
        "--network",
        NETWORK,
        "--env",
        f"DATABASE_URL={database_url}",
        "--env",
        f"TEST_DATABASE_URL={database_url}",
        "--mount",
        _mount(control_bundle / "launch-v4-publication-worker.py", CONTROL_LAUNCHER),
        "--mount",
        _mount(lock_path, CONTROL_LOCK),
        "--mount",
        _mount(digest_path, CONTROL_DIGEST),
    ]
    for source, target in extra_mounts:
        command.extend(("--mount", _mount(source, target)))
    command.extend(
        (
            image,
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            CONTROL_LAUNCHER,
            *argv,
        )
    )
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
