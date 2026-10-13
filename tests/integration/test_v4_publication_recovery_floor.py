"""Fail-closed qualification tests for the hold-aware worker floor."""

from __future__ import annotations

import base64
import asyncio
import csv
from dataclasses import asdict, replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import UUID
import zipfile
from types import SimpleNamespace

import pytest

from salience.cycles.publication_recovery_compatibility import (
    PublicationWorkerCompatibilityError,
    PublicationWorkerEntrypoint,
    PublicationWorkerImportPin,
    PublicationWorkerLaunchLock,
    PublicationWorkerPin,
    PublicationWorkerSourcePin,
    require_publication_worker_compatibility,
)


ROOT = Path(__file__).parents[2]
ANCHOR = "69d7ab5da02cc9b8013f17b3e9aed3f7edb758a6"
A4 = "a4b2368cdb463939d093020224a5b5667392aee6"
PREFLOOR = "039ddf39cb9b846c205f352e083293fbec6f9fc5"
SOURCE_PATHS = (
    "scripts/launch-v4-publication-worker.py",
    "src/salience/cycles/publication_recovery_compatibility.py",
    "src/salience/cycles/legacy_runtime.py",
    "src/salience/cycles/legacy_publication_runtime.py",
    "src/salience/cycles/publication_recovery.py",
    "src/salience/cycles/legacy_publication.py",
    "src/salience/workflows/worker.py",
    "src/salience/workflows/publication.py",
    "src/salience/workflows/persistence.py",
    "src/salience/publication/repository.py",
    "migrations/versions/0042_legacy_publication_recovery_holds.py",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _module_for_source(path: str) -> str:
    return path.removeprefix("src/").removesuffix(".py").replace("/", ".")


def _installed_path(path: str) -> str:
    return path.removeprefix("src/")


def _record_digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def _source_manifest_sha256(source_files) -> str:
    payload = [asdict(item) for item in source_files]
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return _sha256(encoded)


def _lock_bytes(lock) -> bytes:
    return (json.dumps(asdict(lock), sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_test_lock(tmp_path, monkeypatch):
    from salience.cycles import publication_recovery_compatibility as compatibility

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    source_pins = []
    import_pins = []
    observed_paths = {}
    copied = []
    for source_name in SOURCE_PATHS:
        source = ROOT / source_name
        data = source.read_bytes()
        source_pins.append(
            PublicationWorkerSourcePin(
                path=source_name, git_blob=_git_blob(data), sha256=_sha256(data)
            )
        )
        target_name = _installed_path(source_name)
        target = runtime_root / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copied.append((target_name, data))
        if source_name.startswith("src/"):
            module = _module_for_source(source_name)
            observed_paths[module] = str(target)
            import_pins.append(
                PublicationWorkerImportPin(
                    module=module,
                    wheel_path=target_name,
                    observed_path=str(target),
                    sha256=_sha256(data),
                )
            )

    dist_info = runtime_root / "salience-0.1.0.dist-info"
    dist_info.mkdir()
    record = dist_info / "RECORD"
    record_rows = []
    for relative, data in copied:
        record_rows.append((relative, "sha256=" + _record_digest(data), str(len(data))))
    record_rows.append((str(record.relative_to(runtime_root)), "", ""))
    with record.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(record_rows)
    record_data = record.read_bytes()

    wheel_path = tmp_path / "salience.whl"
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in copied:
            archive.writestr(relative, data)
        archive.writestr(
            "salience-0.1.0.dist-info/RECORD",
            record_data,
        )
    source_files = tuple(source_pins)
    pin = PublicationWorkerPin(
        schema_version="salience.publication-worker-pin.v1",
        application_commit=os.environ.get("TEST_PUBLICATION_FLOOR_COMMIT", ANCHOR),
        eligible_anchor=ANCHOR,
        artifact_path="/opt/salience-artifacts/salience.whl",
        artifact_sha256=_sha256(wheel_path.read_bytes()),
        distribution_record_path=str(record),
        distribution_record_sha256=_sha256(record_data),
        source_manifest_sha256=_source_manifest_sha256(source_files),
        source_files=source_files,
        runtime_imports=tuple(import_pins),
    )
    launcher_data = (ROOT / "scripts/launch-v4-publication-worker.py").read_bytes()
    lock = PublicationWorkerLaunchLock(
        schema_version="salience.publication-worker-launch-lock.v1",
        launcher_sha256=_sha256(launcher_data),
        interpreter_path=sys.executable,
        interpreter_sha256=_sha256(Path(sys.executable).read_bytes()),
        runtime_root=str(runtime_root),
        workers={
            "deployable": PublicationWorkerEntrypoint(
                module="salience.workflows.worker", argv=()
            ),
            "legacy-fixture": PublicationWorkerEntrypoint(
                module="salience.cycles.legacy_runtime", argv=()
            ),
        },
        pin=pin,
    )
    monkeypatch.setattr(compatibility, "_artifact_file_path", lambda _pin: wheel_path)
    monkeypatch.setattr(
        compatibility,
        "_launcher_file_path",
        lambda: ROOT / "scripts/launch-v4-publication-worker.py",
    )
    monkeypatch.setattr(
        compatibility,
        "_observed_import_path",
        lambda module: observed_paths[module],
    )
    return lock, wheel_path, runtime_root, observed_paths


def _launcher_module():
    launcher_path = ROOT / "scripts/launch-v4-publication-worker.py"
    spec = importlib.util.spec_from_file_location("publication_worker_launcher", launcher_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure_launcher(launcher, tmp_path, lock, wheel_path, monkeypatch):
    lock_path = tmp_path / "control" / "publication-worker-launch-lock.json"
    digest_path = tmp_path / "control" / "publication-worker-launch-lock.sha256"
    launcher_path = ROOT / "scripts/launch-v4-publication-worker.py"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    raw = _lock_bytes(lock)
    lock_path.write_bytes(raw)
    digest = _sha256(raw)
    digest_path.write_text(digest + "\n", encoding="ascii")
    monkeypatch.setattr(launcher, "CONTROL_LAUNCHER_PATH", launcher_path)
    monkeypatch.setattr(launcher, "CONTROL_LOCK_PATH", lock_path)
    monkeypatch.setattr(launcher, "CONTROL_LOCK_DIGEST_PATH", digest_path)
    monkeypatch.setattr(launcher, "RETAINED_WHEEL_PATH", wheel_path)
    monkeypatch.setattr(launcher, "FIXED_INTERPRETER_PATH", sys.executable)
    return lock_path, digest_path, launcher_path, digest


@pytest.fixture
def active_0042_database(tmp_path):
    from test_v4_publication_recovery_holds import (
        _create_database,
        _drop_database,
        _migrate,
    )

    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_floor_active_")
    upgraded = _migrate(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with __import__("psycopg").connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_recovery_holds"
        ).fetchone()[0] == 0
    try:
        yield database
    finally:
        _drop_database(source, name)


def test_floor_gate_rejects_a4_unknown_unpinned_unrelated_and_pre_floor_before_start(
    active_0042_database, tmp_path, monkeypatch
):
    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    original_digest = _sha256(_lock_bytes(lock))
    candidates = (
        (replace(lock, pin=replace(lock.pin, application_commit=A4)), None),
        (replace(lock, pin=replace(lock.pin, application_commit="f" * 40)), original_digest),
        (replace(lock, pin=replace(lock.pin, eligible_anchor="1" * 40)), None),
        (replace(lock, pin=replace(lock.pin, application_commit=PREFLOOR)), None),
    )
    for candidate, trusted_digest in candidates:
        started = []
        with pytest.raises(PublicationWorkerCompatibilityError):
            result = require_publication_worker_compatibility(
                active_0042_database,
                start_mode="current",
                launch_lock=candidate,
                observed_launch_lock_sha256=trusted_digest or _sha256(_lock_bytes(candidate)),
            )
            started.append(result)
        assert started == []


def test_floor_gate_requires_exact_artifact_install_source_and_runtime_import_pins(
    active_0042_database, tmp_path, monkeypatch
):
    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    bad_pins = [
        replace(lock.pin, artifact_path="/tmp/candidate.whl"),
        replace(lock.pin, artifact_sha256="0" * 64),
        replace(lock.pin, distribution_record_path="/tmp/RECORD"),
        replace(lock.pin, distribution_record_sha256="0" * 64),
        replace(lock.pin, source_manifest_sha256="0" * 64),
        replace(
            lock.pin,
            runtime_imports=(replace(lock.pin.runtime_imports[0], sha256="0" * 64),)
            + lock.pin.runtime_imports[1:],
        ),
        replace(
            lock.pin,
            runtime_imports=(replace(lock.pin.runtime_imports[0], observed_path="/a4/salience.py"),)
            + lock.pin.runtime_imports[1:],
        ),
        replace(
            lock.pin,
            runtime_imports=(replace(lock.pin.runtime_imports[0], wheel_path="a4/worker.py"),)
            + lock.pin.runtime_imports[1:],
        ),
    ]
    changed_sources = (replace(lock.pin.source_files[0], git_blob="0" * 40),) + lock.pin.source_files[1:]
    bad_pins.append(
        replace(
            lock.pin,
            source_files=changed_sources,
            source_manifest_sha256=_source_manifest_sha256(changed_sources),
        )
    )
    for bad_pin in bad_pins:
        candidate = replace(lock, pin=bad_pin)
        observed = _sha256(_lock_bytes(candidate))
        with pytest.raises(PublicationWorkerCompatibilityError):
            require_publication_worker_compatibility(
                active_0042_database,
                start_mode="current",
                launch_lock=candidate,
                observed_launch_lock_sha256=observed,
            )
    runtime_root = Path(lock.runtime_root)
    import_path = runtime_root / lock.pin.runtime_imports[0].wheel_path
    original = import_path.read_bytes()
    import_path.write_bytes(original + b"# modified after RECORD\n")
    try:
        with pytest.raises(PublicationWorkerCompatibilityError):
            require_publication_worker_compatibility(
                active_0042_database,
                start_mode="current",
                launch_lock=lock,
                observed_launch_lock_sha256=_sha256(_lock_bytes(lock)),
            )
    finally:
        import_path.write_bytes(original)


def test_launcher_binds_valid_floor_lock_to_fixed_interpreter_runtime_and_entrypoint(
    tmp_path, monkeypatch
):
    launcher = _launcher_module()
    lock, _, runtime_root, _ = _write_test_lock(tmp_path, monkeypatch)
    _, _, _, digest = _configure_launcher(
        launcher, tmp_path, lock, tmp_path / "salience.whl", monkeypatch
    )
    # _write_test_lock returns the retained wheel separately from its lock.
    wheel_path = Path(launcher.RETAINED_WHEEL_PATH)
    assert _sha256(wheel_path.read_bytes()) == lock.pin.artifact_sha256
    executed = []
    working_directories = []
    environments = []

    class ExecReached(Exception):
        pass

    def capture_exec(path, argv, environment):
        executed.append((path, argv))
        environments.append(environment)
        raise ExecReached

    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stderr": ""})())
    monkeypatch.setattr(launcher.os, "chdir", lambda path: working_directories.append(Path(path)))
    monkeypatch.setattr(launcher.os, "execve", capture_exec)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    shadow = tmp_path / "shadow"
    (shadow / "salience" / "workflows").mkdir(parents=True)
    (shadow / "salience" / "workflows" / "worker.py").write_text("raise RuntimeError('shadow')")
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    monkeypatch.setenv("PYTHONHOME", str(shadow))
    with pytest.raises(ExecReached):
        launcher.main(["--mode", "current", "--worker-kind", "deployable"])
    assert executed == [
        (
            lock.interpreter_path,
            [lock.interpreter_path, "-I", "-m", "salience.workflows.worker"],
        )
    ]
    assert working_directories == [runtime_root]
    assert "PYTHONPATH" not in environments[0] and "PYTHONHOME" not in environments[0]
    assert environments[0]["SALIENCE_PUBLICATION_WORKER_START_MODE"] == "current"
    assert environments[0]["SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256"] == digest
    for arguments in (
        ["--mode", "current", "--worker-kind", "deployable", "--executable", "/a4/python"],
        ["--mode", "current", "--worker-kind", "deployable", "--module", "a4.worker"],
        ["--mode", "current", "--worker-kind", "deployable", "--runtime-root", "/a4"],
        ["--mode", "current", "--worker-kind", "deployable", "--", "python", "-m", "a4"],
    ):
        with pytest.raises((SystemExit, PublicationWorkerCompatibilityError)):
            launcher.main(arguments)
    assert len(executed) == 1
    assert lock.workers["deployable"].module == "salience.workflows.worker"


def test_launcher_rejects_candidate_selected_lock_and_modified_launcher(tmp_path, monkeypatch):
    launcher = _launcher_module()
    lock, wheel_path, _, _ = _write_test_lock(tmp_path, monkeypatch)
    lock_path, digest_path, source_path, trusted_digest = _configure_launcher(
        launcher, tmp_path, lock, wheel_path, monkeypatch
    )
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    executed = []
    monkeypatch.setattr(launcher.os, "execve", lambda *args: executed.append(args))
    candidate = replace(lock, pin=replace(lock.pin, application_commit=A4))
    lock_path.write_bytes(_lock_bytes(candidate))
    monkeypatch.setenv(
        "SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256",
        _sha256(_lock_bytes(candidate)),
    )
    with pytest.raises((SystemExit, launcher.LauncherError, PublicationWorkerCompatibilityError)):
        launcher.main(["--mode", "current", "--worker-kind", "deployable"])
    assert digest_path.read_text().strip() == trusted_digest
    lock_path.write_bytes(_lock_bytes(lock))
    modified_launcher = tmp_path / "modified-launcher.py"
    modified_launcher.write_bytes(source_path.read_bytes() + b"# candidate modification\n")
    monkeypatch.setattr(launcher, "CONTROL_LAUNCHER_PATH", modified_launcher)
    with pytest.raises((SystemExit, launcher.LauncherError, PublicationWorkerCompatibilityError)):
        launcher.main(["--mode", "current", "--worker-kind", "deployable"])
    with pytest.raises((SystemExit, launcher.LauncherError, PublicationWorkerCompatibilityError)):
        launcher.main(
            ["--mode", "current", "--worker-kind", "deployable", "--lock", "/tmp/candidate-lock.json"]
        )
    assert executed == []
    assert lock.launcher_sha256 == _sha256(
        (ROOT / "scripts/launch-v4-publication-worker.py").read_bytes()
    )


def test_floor_gate_requires_workspace_and_both_reason_verification_for_rollback_restart(
    active_0042_database, tmp_path, monkeypatch
):
    from salience.cycles import publication_recovery as recovery

    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    observed = _sha256(_lock_bytes(lock))
    for workspace, operation_ids in (
        (None, None),
        ("00000000-0000-0000-0000-000000000001", None),
        ("00000000-0000-0000-0000-000000000001", {"expired_permit_receipt": "00000000-0000-0000-0000-000000000002"}),
        (
            "00000000-0000-0000-0000-000000000001",
            {
                "expired_permit_receipt": "00000000-0000-0000-0000-000000000002",
                "published_during_cancel": "00000000-0000-0000-0000-000000000002",
            },
        ),
    ):
        with pytest.raises(PublicationWorkerCompatibilityError):
            require_publication_worker_compatibility(
                active_0042_database,
                start_mode="rollback-restart",
                launch_lock=lock,
                observed_launch_lock_sha256=observed,
                rollback_workspace_id=workspace,
                rollback_operation_ids=operation_ids,
            )

    calls = []
    monkeypatch.setattr(
        recovery,
        "verify_publication_recovery_rollback",
        lambda database_url, **kwargs: calls.append((database_url, kwargs)) or ("expired", "cancel"),
    )
    require_publication_worker_compatibility(
        active_0042_database,
        start_mode="rollback-restart",
        launch_lock=lock,
        observed_launch_lock_sha256=observed,
        rollback_workspace_id="00000000-0000-0000-0000-000000000001",
        rollback_operation_ids={
            "expired_permit_receipt": "00000000-0000-0000-0000-000000000002",
            "published_during_cancel": "00000000-0000-0000-0000-000000000003",
        },
    )
    assert calls == [
        (
            active_0042_database,
            {
                "workspace_id": __import__("uuid").UUID("00000000-0000-0000-0000-000000000001"),
                "operation_ids": {
                    "expired_permit_receipt": UUID("00000000-0000-0000-0000-000000000002"),
                    "published_during_cancel": UUID("00000000-0000-0000-0000-000000000003"),
                },
            },
        )
    ]


def test_current_mode_rejects_rollback_arguments_and_rollback_mode_always_verifies(
    active_0042_database, tmp_path, monkeypatch
):
    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    observed = _sha256(_lock_bytes(lock))
    for workspace, operation_ids in (
        ("00000000-0000-0000-0000-000000000001", None),
        (None, {"expired_permit_receipt": "00000000-0000-0000-0000-000000000002"}),
    ):
        with pytest.raises(PublicationWorkerCompatibilityError):
            require_publication_worker_compatibility(
                active_0042_database,
                start_mode="current",
                launch_lock=lock,
                observed_launch_lock_sha256=observed,
                rollback_workspace_id=workspace,
                rollback_operation_ids=operation_ids,
            )


def test_docker_and_compose_worker_use_pinned_wheel_and_trusted_launcher():
    import yaml

    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert "ARG SALIENCE_WHEEL_FILE" in dockerfile
    assert "/opt/salience-artifacts/salience.whl" in dockerfile
    assert "pip install ." not in dockerfile
    command_line = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    docker_command = json.loads(command_line.removeprefix("CMD "))
    expected_command = [
        "/usr/local/bin/python",
        "/run/salience-control/launch-v4-publication-worker.py",
        "--mode",
        "current",
        "--worker-kind",
        "deployable",
    ]
    assert docker_command == expected_command
    services = compose["services"]
    for service in ("api", "worker", "migrate", "mock-effect-provider"):
        assert services[service]["build"]["args"]["SALIENCE_WHEEL_FILE"]
    assert services["worker"]["command"] == expected_command
    assert '"python", "-m", "salience.workflows.worker"' not in (ROOT / "compose.yaml").read_text()
    assert any(
        "/run/salience-control/publication-worker-launch-lock.sha256:ro" in volume
        for volume in services["worker"]["volumes"]
    )


def test_pre_0042_database_returns_no_floor_requirement(tmp_path, monkeypatch):
    from test_v4_publication_recovery_holds import (
        _create_database,
        _drop_database,
        _migrate,
    )

    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_floor_pre_0042_")
    try:
        before = _migrate(database, "upgrade", "0041_native_publication_sources")
        assert before.returncode == 0, before.stderr
        assert require_publication_worker_compatibility(
            database,
            start_mode="current",
            launch_lock=lock,
            observed_launch_lock_sha256=_sha256(_lock_bytes(lock)),
        ) is None
    finally:
        _drop_database(source, name)


@pytest.mark.asyncio
async def test_worker_entrypoints_fail_closed_before_temporal_connect_at_0042(
    active_0042_database, monkeypatch
):
    from salience.cycles import legacy_runtime
    from salience.workflows import worker

    for name in (
        "SALIENCE_PUBLICATION_WORKER_START_MODE",
        "SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK",
        "SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256",
        "SALIENCE_PUBLICATION_ROLLBACK_WORKSPACE_ID",
        "SALIENCE_PUBLICATION_EXPIRED_HOLD_OPERATION_ID",
        "SALIENCE_PUBLICATION_CANCEL_HOLD_OPERATION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", active_0042_database)
    monkeypatch.setenv("TEST_DATABASE_URL", active_0042_database)
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setattr(legacy_runtime, "require_fixture", lambda: None)
    monkeypatch.setattr(
        worker.Settings,
        "from_environment",
        staticmethod(lambda: SimpleNamespace(database_url=active_0042_database)),
    )
    temporal_connects = []

    async def forbidden_connect(*args, **kwargs):
        temporal_connects.append((args, kwargs))
        raise AssertionError("worker must fail before Temporal connect")

    monkeypatch.setattr(legacy_runtime.Client, "connect", forbidden_connect)
    with pytest.raises(PublicationWorkerCompatibilityError):
        await legacy_runtime.main()
    monkeypatch.setattr(worker.Client, "connect", forbidden_connect)
    with pytest.raises(PublicationWorkerCompatibilityError):
        await worker.run_worker()
    assert temporal_connects == []


def test_migration_graph_rejects_branch_and_cycle_shapes():
    from salience.cycles import publication_recovery_compatibility as compatibility

    validate_graph = getattr(compatibility, "_validate_migration_graph", None)
    assert callable(validate_graph), "migration graph validation must be explicit and testable"
    for graph in (
        {
            "root": (),
            "floor": ("root",),
            "side": ("root",),
            "merge": ("floor", "side"),
        },
        {"root": ("next",), "next": ("root",)},
        {"root": (), "detached": ()},
    ):
        with pytest.raises(PublicationWorkerCompatibilityError):
            validate_graph(graph)


def test_installed_migration_graph_resolves_inside_site_packages(tmp_path, monkeypatch):
    from salience.cycles import publication_recovery_compatibility as compatibility

    site_packages = tmp_path / "lib" / "python3.13" / "site-packages"
    package_file = site_packages / "salience" / "cycles" / "publication_recovery_compatibility.py"
    package_file.parent.mkdir(parents=True)
    package_file.touch()
    migration_versions = site_packages / "migrations" / "versions"
    shutil.copytree(ROOT / "migrations" / "versions", migration_versions)
    monkeypatch.setattr(compatibility, "__file__", str(package_file))

    revisions, heads = compatibility._migration_graph()

    assert "0042_publication_recovery_holds" in revisions
    assert heads == {"0042_publication_recovery_holds"}


def test_inprocess_gate_wraps_malformed_rollback_environment_as_compatibility_error(
    tmp_path, monkeypatch
):
    from salience.cycles import publication_recovery_compatibility as compatibility

    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    lock_path = tmp_path / "control" / "publication-worker-launch-lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(_lock_bytes(lock))
    digest = _sha256(_lock_bytes(lock))
    monkeypatch.setattr(compatibility, "PUBLICATION_WORKER_LOCK_PATH", str(lock_path))
    monkeypatch.setattr(compatibility, "_database_revision", lambda _database_url: "0042_publication_recovery_holds")
    monkeypatch.setenv(compatibility.PUBLICATION_WORKER_START_MODE_ENV, "rollback-restart")
    monkeypatch.setenv(compatibility.PUBLICATION_WORKER_LAUNCH_LOCK_ENV, str(lock_path))
    monkeypatch.setenv(compatibility.PUBLICATION_WORKER_LAUNCH_LOCK_SHA256_ENV, digest)
    monkeypatch.setenv(compatibility.PUBLICATION_ROLLBACK_WORKSPACE_ENV, "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv(compatibility.PUBLICATION_EXPIRED_HOLD_OPERATION_ENV, "not-a-uuid")
    monkeypatch.setenv(compatibility.PUBLICATION_CANCEL_HOLD_OPERATION_ENV, "00000000-0000-0000-0000-000000000003")

    with pytest.raises(PublicationWorkerCompatibilityError):
        compatibility.require_publication_worker_compatibility_from_environment("unused")
