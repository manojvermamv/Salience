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
import marshal
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import subprocess
import struct
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
BASE_SOURCE_PATHS = (
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
)
MIGRATION_SUPPORT_PATHS = (
    "alembic.ini",
    "migrations/__init__.py",
    "migrations/env.py",
    "migrations/versions/__init__.py",
)
APPLICATION_PACKAGE_PATHS = tuple(
    path.relative_to(ROOT).as_posix()
    for path in sorted((ROOT / "src" / "salience").rglob("*"))
    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
)
MIGRATION_REVISION_PATHS = tuple(
    path.relative_to(ROOT).as_posix()
    for path in sorted((ROOT / "migrations" / "versions").glob("[0-9][0-9][0-9][0-9]_*.py"))
)
SOURCE_PATHS = tuple(sorted(set(
    BASE_SOURCE_PATHS
    + APPLICATION_PACKAGE_PATHS
    + MIGRATION_SUPPORT_PATHS
    + MIGRATION_REVISION_PATHS
)))


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


def test_legacy_activity_retry_readiness_matches_exact_temporal_execution():
    from temporalio.api.enums.v1 import PendingActivityState, TimeoutType

    from test_v4_legacy_publication import _pending_activity_retry_ready

    workflow_id = "salience-v4-legacy-operation:operation-1"
    run_id = "run-1"
    activity_id = "4"
    activity_type = "salience.publication.submit_or_reconcile"

    def description(
        *,
        observed_workflow_id=workflow_id,
        observed_run_id=run_id,
        observed_activity_id=activity_id,
        observed_activity_type=activity_type,
        attempt=2,
        state=PendingActivityState.PENDING_ACTIVITY_STATE_SCHEDULED,
        timeout_type=TimeoutType.TIMEOUT_TYPE_START_TO_CLOSE,
    ):
        pending = SimpleNamespace(
            activity_id=observed_activity_id,
            activity_type=SimpleNamespace(name=observed_activity_type),
            attempt=attempt,
            state=state,
            last_failure=SimpleNamespace(
                timeout_failure_info=SimpleNamespace(timeout_type=timeout_type)
            ),
        )
        return SimpleNamespace(
            id=observed_workflow_id,
            run_id=observed_run_id,
            raw_description=SimpleNamespace(pending_activities=[pending]),
        )

    expected = {
        "workflow_id": workflow_id,
        "run_id": run_id,
        "activity_id": activity_id,
        "activity_type": activity_type,
        "attempt": 2,
    }
    assert _pending_activity_retry_ready(description(), **expected)
    assert not _pending_activity_retry_ready(
        description(observed_workflow_id="another-workflow"), **expected
    )
    assert not _pending_activity_retry_ready(
        description(observed_run_id="another-run"), **expected
    )
    assert not _pending_activity_retry_ready(
        description(observed_activity_id="another-activity"), **expected
    )
    assert not _pending_activity_retry_ready(
        description(observed_activity_type="salience.publication.cancel"), **expected
    )
    assert not _pending_activity_retry_ready(description(attempt=1), **expected)
    assert not _pending_activity_retry_ready(description(attempt=3), **expected)
    assert not _pending_activity_retry_ready(
        description(state=PendingActivityState.PENDING_ACTIVITY_STATE_STARTED),
        **expected,
    )
    assert not _pending_activity_retry_ready(
        description(timeout_type=TimeoutType.TIMEOUT_TYPE_SCHEDULE_TO_START),
        **expected,
    )


def _lock_bytes(lock) -> bytes:
    return (json.dumps(asdict(lock), sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_test_lock(tmp_path, monkeypatch, *, include_interpreter_prefix_record=False):
    """Create synthetic authenticated bytes for unit tests, not commit-bound evidence."""
    from salience.cycles import publication_recovery_compatibility as compatibility

    if include_interpreter_prefix_record:
        interpreter_installation = tmp_path / "test-python-prefix"
        runtime_root = interpreter_installation / "lib/python3.13/site-packages"
        runtime_root.mkdir(parents=True)
        interpreter_path = interpreter_installation / "bin/python"
        interpreter_path.parent.mkdir(parents=True)
        interpreter_path.write_bytes(b"synthetic fixed interpreter bytes\n")
    else:
        runtime_root = tmp_path / "runtime"
        runtime_root.mkdir()
        interpreter_path = Path(sys.executable)
    source_pins = []
    import_pins = []
    observed_paths = {}
    copied = []
    recorded_files = []
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
            recorded_files.append((target_name, data))
        if source_name.startswith("src/") and source_name.endswith(".py"):
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
    for relative, data in recorded_files:
        record_rows.append((relative, "sha256=" + _record_digest(data), str(len(data))))
    if include_interpreter_prefix_record:
        prefix_file = interpreter_installation / "bin/content"
        prefix_content = b"synthetic retained console script\n"
        prefix_file.write_bytes(prefix_content)
        prefix_relative = os.path.relpath(prefix_file, runtime_root)
        record_rows.append(
            (prefix_relative, "sha256=" + _record_digest(prefix_content), str(len(prefix_content)))
        )
    record_rows.append((str(record.relative_to(runtime_root)), "", ""))
    with record.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(record_rows)
    record_data = record.read_bytes()

    wheel_path = tmp_path / "salience.whl"
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in recorded_files:
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
        interpreter_path=str(interpreter_path),
        interpreter_sha256=_sha256(interpreter_path.read_bytes()),
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
    if not include_interpreter_prefix_record:
        monkeypatch.setattr(
            compatibility,
            "_interpreter_installation_prefix",
            lambda _path: runtime_root.parent.resolve(),
        )
    return lock, wheel_path, runtime_root, observed_paths


def _write_local_launcher_process_fixture(tmp_path, monkeypatch, *, allow_worker=False):
    """Build a synthetic, no-effects bundle for real launcher subprocess tests."""
    lock, wheel_path, source_root, _ = _write_test_lock(tmp_path, monkeypatch)
    prefix = tmp_path / "python-prefix"
    interpreter_path = prefix / "bin" / "python"
    interpreter_path.parent.mkdir(parents=True)
    shutil.copy2(Path(sys.executable).resolve(), interpreter_path)
    (prefix / "pyvenv.cfg").write_text(
        "home = /usr\ninclude-system-site-packages = false\nversion = 3.13.5\n",
        encoding="utf-8",
    )
    runtime_root = prefix / "lib" / "python3.13" / "site-packages"
    runtime_root.parent.mkdir(parents=True)
    shutil.copytree(source_root, runtime_root)

    control_bundle = tmp_path / "control-bundle"
    control_bundle.mkdir()
    launcher_path = control_bundle / "launch-v4-publication-worker.py"
    lock_path = control_bundle / "publication-worker-launch-lock.json"
    digest_path = control_bundle / "publication-worker-launch-lock.sha256"
    preflight_import_marker = tmp_path / "preflight-imported"
    site_marker = tmp_path / "site-startup-ran"
    worker_marker = tmp_path / "worker-started"

    launcher_text = (ROOT / "scripts/launch-v4-publication-worker.py").read_text(
        encoding="utf-8"
    )
    replacements = {
        'CONTROL_LAUNCHER_PATH = Path("/run/salience-control/launch-v4-publication-worker.py")':
            f"CONTROL_LAUNCHER_PATH = Path({str(launcher_path)!r})",
        'CONTROL_LOCK_PATH = Path("/run/salience-control/publication-worker-launch-lock.json")':
            f"CONTROL_LOCK_PATH = Path({str(lock_path)!r})",
        'CONTROL_LOCK_DIGEST_PATH = Path("/run/salience-control/publication-worker-launch-lock.sha256")':
            f"CONTROL_LOCK_DIGEST_PATH = Path({str(digest_path)!r})",
        'RETAINED_WHEEL_PATH = Path("/opt/salience-artifacts/salience.whl")':
            f"RETAINED_WHEEL_PATH = Path({str(wheel_path)!r})",
        'FIXED_INTERPRETER_PATH = "/usr/local/bin/python"':
            f"FIXED_INTERPRETER_PATH = {str(interpreter_path)!r}",
        'FIXED_RUNTIME_ROOT = Path("/usr/local/lib/python3.13/site-packages")':
            f"FIXED_RUNTIME_ROOT = Path({str(runtime_root)!r})",
    }
    for original, replacement_text in replacements.items():
        launcher_text = launcher_text.replace(original, replacement_text)
    launcher_data = launcher_text.encode()
    launcher_path.write_bytes(launcher_data)

    compatibility_source = f"""from pathlib import Path
import sys
Path({str(preflight_import_marker)!r}).write_text(
    f"{{sys.flags.isolated}},{{sys.flags.dont_write_bytecode}},{{sys.flags.no_site}}",
    encoding="utf-8",
)

def _launch_lock_from_json(payload):
    return payload

def require_publication_worker_compatibility(*args, **kwargs):
    {"return None" if allow_worker else 'raise RuntimeError("synthetic preflight denial")'}
""".encode()
    worker_source = f"""from pathlib import Path
import sys
Path({str(worker_marker)!r}).write_text(
    f"{{sys.flags.isolated}},{{sys.flags.dont_write_bytecode}},{{sys.flags.no_site}}",
    encoding="utf-8",
)
""".encode()
    runtime_files = {
        "src/salience/cycles/publication_recovery_compatibility.py": compatibility_source,
        "src/salience/cycles/legacy_runtime.py": worker_source,
        "src/salience/workflows/worker.py": worker_source,
    }
    for source_name, data in runtime_files.items():
        (runtime_root / _installed_path(source_name)).write_bytes(data)

    record_path = runtime_root / Path(lock.pin.distribution_record_path).relative_to(source_root)
    old_rows = list(csv.reader(record_path.read_text(encoding="utf-8").splitlines()))
    record_relative = str(record_path.relative_to(runtime_root))
    recorded_paths = [row[0] for row in old_rows if row[0] != record_relative]
    record_rows = []
    for relative in recorded_paths:
        content = (runtime_root / relative).read_bytes()
        record_rows.append((relative, "sha256=" + _record_digest(content), str(len(content))))
    record_rows.append((record_relative, "", ""))
    with record_path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(record_rows)
    record_data = record_path.read_bytes()
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in recorded_paths:
            archive.write(runtime_root / relative, relative)
        archive.writestr(record_relative, record_data)
    wheel_bytes = wheel_path.read_bytes()

    updated_source_data = {
        "scripts/launch-v4-publication-worker.py": launcher_data,
        **runtime_files,
    }
    source_files = tuple(
        sorted(
            (
                replace(
                    item,
                    git_blob=_git_blob(updated_source_data[item.path]),
                    sha256=_sha256(updated_source_data[item.path]),
                )
                if item.path in updated_source_data
                else item
                for item in lock.pin.source_files
            ),
            key=lambda item: item.path,
        )
    )
    runtime_imports = tuple(
        replace(
            item,
            observed_path=str(runtime_root / item.wheel_path),
            sha256=_sha256(runtime_files.get(
                "src/" + item.wheel_path,
                (runtime_root / item.wheel_path).read_bytes(),
            )),
        )
        for item in lock.pin.runtime_imports
    )
    pin = replace(
        lock.pin,
        artifact_sha256=_sha256(wheel_bytes),
        distribution_record_path=str(record_path),
        distribution_record_sha256=_sha256(record_data),
        source_files=source_files,
        source_manifest_sha256=_source_manifest_sha256(source_files),
        runtime_imports=runtime_imports,
    )
    lock = replace(
        lock,
        launcher_sha256=_sha256(launcher_data),
        interpreter_path=str(interpreter_path),
        interpreter_sha256=_sha256(interpreter_path.read_bytes()),
        runtime_root=str(runtime_root),
        pin=pin,
    )
    raw_lock = _lock_bytes(lock)
    lock_path.write_bytes(raw_lock)
    digest_path.write_text(_sha256(raw_lock) + "\n", encoding="ascii")

    (runtime_root / "00-hostile-startup.pth").write_text(
        f"import pathlib; pathlib.Path({str(site_marker)!r}).write_text('site', encoding='utf-8')\n",
        encoding="utf-8",
    )
    return {
        "lock": lock,
        "wheel": wheel_path,
        "runtime_root": runtime_root,
        "interpreter": interpreter_path,
        "control_bundle": control_bundle,
        "launcher": launcher_path,
        "lock_path": lock_path,
        "digest_path": digest_path,
        "preflight_import_marker": preflight_import_marker,
        "site_marker": site_marker,
        "worker_marker": worker_marker,
    }


def _run_local_supported_launcher(
    fixture, *, worker_kind="legacy-fixture", cwd=None, extra_environment=None
):
    database_url = "postgresql://no-effects.invalid/local-fixture"
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("PYTHON") or key.startswith("SALIENCE_PUBLICATION_"):
            environment.pop(key)
    environment["DATABASE_URL"] = database_url
    environment["TEST_DATABASE_URL"] = database_url
    environment.update(extra_environment or {})
    return subprocess.run(
        [
            str(fixture["interpreter"]),
            "-I",
            "-B",
            "-S",
            str(fixture["launcher"]),
            "--mode",
            "current",
            "--worker-kind",
            worker_kind,
        ],
        cwd=cwd or fixture["runtime_root"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _rewrite_process_fixture_lock(fixture, update):
    lock = json.loads(fixture["lock_path"].read_text(encoding="utf-8"))
    update(lock)
    raw = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fixture["lock_path"].write_bytes(raw)
    fixture["digest_path"].write_text(_sha256(raw) + "\n", encoding="ascii")


def _with_pinned_source(lock, source_name, data, *, write_runtime=True):
    runtime_root = Path(lock.runtime_root)
    installed_name = _installed_path(source_name)
    target = runtime_root / installed_name
    if write_runtime:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    source_pin = PublicationWorkerSourcePin(
        path=source_name,
        git_blob=_git_blob(data),
        sha256=_sha256(data),
    )
    source_files = [item for item in lock.pin.source_files if item.path != source_name]
    source_files.append(source_pin)
    source_files.sort(key=lambda item: item.path)
    source_files = tuple(source_files)
    return replace(
        lock,
        pin=replace(
            lock.pin,
            source_files=source_files,
            source_manifest_sha256=_source_manifest_sha256(source_files),
        ),
    )


def _add_migration_source(lock, filename, metadata):
    source_name = f"migrations/versions/{filename}"
    source = (
        f"revision = {metadata['revision']!r}\n"
        f"down_revision = {metadata['down_revision']!r}\n"
    ).encode()
    return _with_pinned_source(lock, source_name, source)


def _compatibility_registration_probe(lock, database_revision, monkeypatch, *, digest=None):
    from salience.cycles import publication_recovery_compatibility as compatibility

    reads = []
    monkeypatch.setattr(
        compatibility,
        "_database_revision",
        lambda _database_url: reads.append(database_revision) or database_revision,
    )
    registered = []

    def register_worker():
        pin = require_publication_worker_compatibility(
            "unused",
            start_mode="current",
            launch_lock=lock,
            observed_launch_lock_sha256=digest or _sha256(_lock_bytes(lock)),
        )
        registered.append(pin)
        return pin

    return register_worker, reads, registered


def _lock_builder_module():
    builder_path = ROOT / "scripts" / "build_publication_worker_launch_lock.py"
    assert builder_path.is_file(), "publication worker launch-lock builder must be available"
    spec = importlib.util.spec_from_file_location("publication_worker_lock_builder", builder_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
    monkeypatch.setattr(launcher, "FIXED_RUNTIME_ROOT", Path(lock.runtime_root))
    # These tests exercise main() as a unit. The real startup flags are covered
    # by the subprocess tests below.
    monkeypatch.setattr(launcher, "_require_isolated_startup", lambda: None)
    monkeypatch.setattr(
        launcher,
        "_interpreter_installation_prefix",
        lambda: Path(lock.runtime_root).parent.resolve(),
    )
    return lock_path, digest_path, launcher_path, digest


_TASK8_MONOLITHIC_ARTIFACTS = (
    "image-attestation.json",
    "worker-image-id.txt",
    "control-bundle/launch-v4-publication-worker.py",
    "control-bundle/publication-worker-launch-lock.json",
    "control-bundle/publication-worker-launch-lock.sha256",
)


def _require_task8_monolithic_prerequisites(*, candidate_root, environment):
    from urllib.parse import urlsplit

    candidate_root = Path(candidate_root)
    artifacts = [
        relative
        for relative in _TASK8_MONOLITHIC_ARTIFACTS
        if (candidate_root / relative).is_file()
    ]
    missing_artifacts = [
        relative
        for relative in _TASK8_MONOLITHIC_ARTIFACTS
        if not (candidate_root / relative).is_file()
    ]
    if not artifacts:
        pytest.skip("Task 8 candidate state is wholly absent")
    if missing_artifacts:
        raise AssertionError(
            "partial Task 8 candidate state: " + ", ".join(missing_artifacts)
        )
    missing_recorder = [
        name
        for name in ("TEST_ADAPTER_RECORDER_URL", "TEST_ADAPTER_RECORDER_RUN_ID")
        if not environment.get(name)
    ]
    if missing_recorder:
        pytest.skip(
            "Task 8 recorder prerequisites are absent: " + ", ".join(missing_recorder)
        )

    recorder_url = urlsplit(environment["TEST_ADAPTER_RECORDER_URL"])
    assert recorder_url.scheme == "http" and recorder_url.hostname
    assert recorder_url.username is None and recorder_url.password is None
    assert recorder_url.path in {"", "/"}
    assert not recorder_url.query and not recorder_url.fragment
    recorder_run_id = str(UUID(environment["TEST_ADAPTER_RECORDER_RUN_ID"]))
    return {
        "candidate_root": candidate_root,
        "recorder_base_url": recorder_url.geturl().rstrip("/"),
        "recorder_run_id": recorder_run_id,
    }


def _task8_monolithic_setup(*, candidate_root, environment, database_factory):
    prerequisites = _require_task8_monolithic_prerequisites(
        candidate_root=candidate_root,
        environment=environment,
    )
    return database_factory(), prerequisites


@pytest.fixture
def task8_monolithic_database(request, task8_candidate_state):
    database, _ = _task8_monolithic_setup(
        candidate_root=ROOT / "artifacts/v4-next-p1/agents/p1-publication-recovery-0043",
        environment=os.environ,
        database_factory=lambda: request.getfixturevalue("active_0042_database"),
    )
    return database


def _write_task8_monolithic_candidate_artifacts(candidate_root):
    for relative in _TASK8_MONOLITHIC_ARTIFACTS:
        path = Path(candidate_root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture candidate prerequisite\n")


def test_task8_monolithic_gate_skips_without_recorder_before_database_setup(tmp_path):
    candidate_root = tmp_path / "candidate"
    _write_task8_monolithic_candidate_artifacts(candidate_root)
    database_setup_calls = []
    with pytest.raises(pytest.skip.Exception, match="TEST_ADAPTER_RECORDER_URL"):
        _task8_monolithic_setup(
            candidate_root=candidate_root,
            environment={"TEST_ADAPTER_RECORDER_RUN_ID": "00000000-0000-0000-0000-000000000001"},
            database_factory=lambda: database_setup_calls.append("created"),
        )
    assert database_setup_calls == []


def test_task8_monolithic_gate_fails_partial_candidate_before_database_setup(tmp_path):
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    (candidate_root / _TASK8_MONOLITHIC_ARTIFACTS[0]).write_bytes(b"partial")
    database_setup_calls = []
    with pytest.raises(AssertionError, match="partial Task 8 candidate state"):
        _task8_monolithic_setup(
            candidate_root=candidate_root,
            environment={"TEST_ADAPTER_RECORDER_URL": "http://127.0.0.1:8081"},
            database_factory=lambda: database_setup_calls.append("created"),
        )
    assert database_setup_calls == []


def test_task8_monolithic_gate_runs_with_candidate_and_recorder_prerequisites(tmp_path):
    candidate_root = tmp_path / "candidate"
    _write_task8_monolithic_candidate_artifacts(candidate_root)
    environment = {
        "TEST_ADAPTER_RECORDER_URL": "http://127.0.0.1:8081",
        "TEST_ADAPTER_RECORDER_RUN_ID": "00000000-0000-0000-0000-000000000001",
    }
    database_setup_calls = []
    database, prerequisites = _task8_monolithic_setup(
        candidate_root=candidate_root,
        environment=environment,
        database_factory=lambda: database_setup_calls.append("created") or "candidate-db",
    )
    assert database == "candidate-db"
    assert database_setup_calls == ["created"]
    assert prerequisites["candidate_root"] == candidate_root
    assert prerequisites["recorder_base_url"] == "http://127.0.0.1:8081"
    assert prerequisites["recorder_run_id"] == environment["TEST_ADAPTER_RECORDER_RUN_ID"]


def test_candidate_prerequisites_skip_only_wholly_absent_state(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "v4_publication_candidate_prerequisites",
        ROOT / "tests/fixtures/v4_publication_candidate_prerequisites.py",
    )
    assert spec is not None and spec.loader is not None
    prerequisites = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prerequisites)

    candidate_root = tmp_path / "absent-candidate"
    runtime_prefix = tmp_path / "absent-runtime"
    inputs = {
        "candidate_root": candidate_root,
        "tracked_lock_path": ROOT / "deployment/v4/publication-worker-launch-lock.json",
        "tracked_launcher_path": ROOT / "scripts/launch-v4-publication-worker.py",
        "runtime_prefix": runtime_prefix,
    }
    assert prerequisites.load_candidate_state(**inputs) is None

    symlink_root = tmp_path / "symlink-candidate"
    symlink_root.symlink_to(tmp_path / "absent-target", target_is_directory=True)
    with pytest.raises(AssertionError, match="candidate root must not be a symlink"):
        prerequisites.load_candidate_state(**{**inputs, "candidate_root": symlink_root})

    candidate_root.mkdir()
    (candidate_root / "worker-image-id.txt").write_text(
        "sha256:" + "a" * 64 + "\n", encoding="ascii"
    )
    with pytest.raises(AssertionError, match="partial Task 8 candidate state"):
        prerequisites.load_candidate_state(**inputs)

    candidate_root = tmp_path / "mismatched-candidate"
    control_bundle = candidate_root / "control-bundle"
    control_bundle.mkdir(parents=True)
    lock_path = ROOT / "deployment/v4/publication-worker-launch-lock.json"
    launcher_path = ROOT / "scripts/launch-v4-publication-worker.py"
    lock_bytes = lock_path.read_bytes()
    launcher_bytes = launcher_path.read_bytes()
    (control_bundle / "launch-v4-publication-worker.py").write_bytes(launcher_bytes)
    (control_bundle / "publication-worker-launch-lock.json").write_bytes(lock_bytes)
    digest = _sha256(lock_bytes)
    (control_bundle / "publication-worker-launch-lock.sha256").write_text(
        digest + "\n", encoding="ascii"
    )
    (candidate_root / "publication-worker-launch-lock.sha256").write_text(
        digest + "\n", encoding="ascii"
    )
    (candidate_root / "salience.whl").write_bytes(b"candidate wheel")
    pin = json.loads(lock_bytes)["pin"]
    (candidate_root / "application-commit.txt").write_text(
        pin["application_commit"] + "\n", encoding="ascii"
    )
    (candidate_root / "worker-image-id.txt").write_text(
        "sha256:" + "b" * 64 + "\n", encoding="ascii"
    )
    attested_image_id = "sha256:" + "a" * 64
    for name, payload in (
        (
            "image-attestation.json",
            {
                "application_commit": pin["application_commit"],
                "image_id": attested_image_id,
                "image_ref": "salience-worker:test",
                "runtime_manifest_sha256": "0" * 64,
                "runtime_root": "/usr/local/lib/python3.13/site-packages",
            },
        ),
        (
            "worker-qualification-progress.json",
            {
                "application_commit": pin["application_commit"],
                "image_id": attested_image_id,
                "launch_lock_sha256": digest,
                "runtime_manifest_sha256": "0" * 64,
                "runtime_root": "/usr/local/lib/python3.13/site-packages",
            },
        ),
    ):
        (candidate_root / name).write_text(
            json.dumps(payload) + "\n", encoding="utf-8"
        )
    inputs["candidate_root"] = candidate_root
    inputs["runtime_prefix"] = tmp_path / "runtime-prefix"
    (inputs["runtime_prefix"] / "usr/local/lib/python3.13/site-packages").mkdir(
        parents=True
    )
    with pytest.raises(AssertionError, match="image ID disagrees"):
        prerequisites.load_candidate_state(
            **inputs,
            image_inspector=lambda _image_ref: attested_image_id,
        )


def test_task8_candidate_prerequisite_fixture_returns_exact_image(task8_candidate_state):
    assert task8_candidate_state["image_id"] == task8_candidate_state["attestation"]["image_id"]
    assert task8_candidate_state["application_commit"] == (
        task8_candidate_state["candidate_root"] / "application-commit.txt"
    ).read_text(encoding="ascii").strip()
    assert task8_candidate_state["application_commit"] == task8_candidate_state["lock"]["pin"][
        "application_commit"
    ]
    assert task8_candidate_state["runtime_manifest_sha256"] == task8_candidate_state[
        "attestation"
    ]["runtime_manifest_sha256"]


def test_non_live_ci_preserves_full_suite_with_recorded_runtime_budget():
    workflow = (ROOT / ".github/workflows/v4-documentation.yml").read_text(
        encoding="utf-8"
    )
    regression_job = workflow.split("  p0-regression:", 1)[1]
    assert re.search(r"(?m)^    timeout-minutes: 45$", regression_job)
    assert (
        "python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration "
        "tests/e2e tests/evals -m 'not live' -q -rs"
    ) in regression_job


@pytest.fixture
def active_0042_database(tmp_path, request):
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
        preserve_primary_qualification_database = (
            os.environ.get("P1_TASK8_PERSIST_FIXTURES") == "1"
            and request.node.originalname
            == "test_floor_worker_executes_both_retained_reasons_and_second_verifier_passes"
        )
        if not preserve_primary_qualification_database:
            _drop_database(source, name)


@pytest.fixture
def task8_empty_0042_database(task8_candidate_state):
    from test_v4_publication_recovery_holds import (
        REVISION,
        TABLE,
        _create_database,
        _drop_database,
        _migrate,
    )

    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_floor_empty_0042_")
    try:
        upgraded = _migrate(database, "upgrade", REVISION)
        assert upgraded.returncode == 0, upgraded.stderr
        with __import__("psycopg").connect(database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == REVISION
            assert connection.execute(
                f"SELECT count(*) FROM public.{TABLE}"
            ).fetchone()[0] == 0
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


def test_worker_floor_rejects_empty_0042_before_registration(tmp_path, monkeypatch):
    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    register_worker, database_reads, registered = _compatibility_registration_probe(
        lock, "0042_publication_recovery_holds", monkeypatch
    )

    with pytest.raises(PublicationWorkerCompatibilityError, match="0043"):
        register_worker()

    assert database_reads == ["0042_publication_recovery_holds"]
    assert registered == []


@pytest.mark.parametrize(
    "case,database_revision",
    (
        ("earlier-revision", "0001_canonical_foundation"),
        ("unknown-revision", "unrecognized_revision"),
        ("branched-history", "0043_complete_publication_recovery_liabilities"),
        ("ambiguous-head", "0043_complete_publication_recovery_liabilities"),
    ),
    ids=("earlier-revision", "unknown-revision", "branched-history", "ambiguous-head"),
)
def test_worker_floor_rejects_unknown_pre_floor_and_ambiguous_lineages_before_registration(
    case, database_revision, tmp_path, monkeypatch
):
    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    if case == "branched-history":
        lock = _add_migration_source(
            lock,
            "0044_branch.py",
            {"revision": "0044_branch", "down_revision": "0042_publication_recovery_holds"},
        )
    elif case == "ambiguous-head":
        lock = _add_migration_source(
            lock,
            "0099_second_root.py",
            {"revision": "0099_second_root", "down_revision": None},
        )
    register_worker, database_reads, registered = _compatibility_registration_probe(
        lock, database_revision, monkeypatch
    )

    with pytest.raises(PublicationWorkerCompatibilityError):
        register_worker()

    assert registered == []
    if case in {"earlier-revision", "unknown-revision"}:
        assert database_reads == [database_revision]
    else:
        assert database_reads == []


@pytest.mark.parametrize(
    "case,database_revision",
    (
        ("exact-0043", "0043_complete_publication_recovery_liabilities"),
        ("qualified-descendant", "0044_qualified_descendant"),
    ),
    ids=("exact-0043", "qualified-descendant"),
)
def test_worker_floor_accepts_0043_and_qualified_descendant(
    case, database_revision, tmp_path, monkeypatch
):
    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    if case == "qualified-descendant":
        lock = _add_migration_source(
            lock,
            "0044_qualified_descendant.py",
            {
                "revision": "0044_qualified_descendant",
                "down_revision": "0043_complete_publication_recovery_liabilities",
            },
        )
    register_worker, database_reads, registered = _compatibility_registration_probe(
        lock, database_revision, monkeypatch
    )

    assert register_worker() == lock.pin

    assert database_reads == [database_revision]
    assert registered == [lock.pin]


@pytest.mark.parametrize(
    "case",
    (
        "extra-valid-descendant",
        "missing-revision",
        "modified-revision-hash",
        "modified-down-revision",
        "symlink-path-escape",
        "independent-digest-mismatch",
    ),
    ids=(
        "extra-valid-descendant",
        "missing-revision",
        "modified-revision-hash",
        "modified-down-revision",
        "symlink-path-escape",
        "independent-digest-mismatch",
    ),
)
def test_authenticated_migration_inventory_rejects_unexpected_state(
    case, tmp_path, monkeypatch
):
    from salience.cycles import publication_recovery_compatibility as compatibility

    lock, _, runtime_root, _ = _write_test_lock(tmp_path, monkeypatch)
    revision_path = runtime_root / "migrations/versions/0043_complete_publication_recovery_liabilities.py"
    observed_digest = None
    if case == "extra-valid-descendant":
        extra_path = runtime_root / "migrations/versions/0044_unpinned.py"
        extra_path.write_text(
            "revision = '0044_unpinned'\ndown_revision = '0043_complete_publication_recovery_liabilities'\n",
            encoding="utf-8",
        )
    elif case == "missing-revision":
        revision_path.unlink()
    elif case == "modified-revision-hash":
        original = next(
            item for item in lock.pin.source_files
            if item.path == "migrations/versions/0043_complete_publication_recovery_liabilities.py"
        )
        changed = replace(original, sha256="0" * 64)
        source_files = tuple(changed if item.path == original.path else item for item in lock.pin.source_files)
        lock = replace(
            lock,
            pin=replace(
                lock.pin,
                source_files=source_files,
                source_manifest_sha256=_source_manifest_sha256(source_files),
            ),
        )
    elif case == "modified-down-revision":
        changed = revision_path.read_bytes().replace(
            b"0042_publication_recovery_holds",
            b"0041_native_publication_sources",
        )
        lock = _with_pinned_source(
            lock,
            "migrations/versions/0043_complete_publication_recovery_liabilities.py",
            changed,
        )
    elif case == "symlink-path-escape":
        escaped = tmp_path / "outside-migration.py"
        escaped.write_bytes(revision_path.read_bytes())
        revision_path.unlink()
        revision_path.symlink_to(escaped)
    elif case == "independent-digest-mismatch":
        observed_digest = "0" * 64

    parse_calls = []
    original_parse = getattr(compatibility, "_parse_revision_metadata", None)
    if original_parse is not None:
        def counted_parse(*args, **kwargs):
            parse_calls.append(True)
            return original_parse(*args, **kwargs)

        monkeypatch.setattr(compatibility, "_parse_revision_metadata", counted_parse)
    register_worker, database_reads, registered = _compatibility_registration_probe(
        lock,
        "0043_complete_publication_recovery_liabilities",
        monkeypatch,
        digest=observed_digest,
    )

    with pytest.raises(PublicationWorkerCompatibilityError):
        register_worker()

    assert registered == []
    assert database_reads == []
    if case == "modified-down-revision":
        assert parse_calls
    else:
        assert parse_calls == []


def test_authenticated_migration_inventory_accepts_exact_0043_inventory(tmp_path, monkeypatch):
    from salience.cycles import publication_recovery_compatibility as compatibility

    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    parsed = []
    original_parse = getattr(compatibility, "_parse_revision_metadata", None)
    if original_parse is not None:
        def counted_parse(*args, **kwargs):
            parsed.append(True)
            return original_parse(*args, **kwargs)

        monkeypatch.setattr(compatibility, "_parse_revision_metadata", counted_parse)
    register_worker, database_reads, registered = _compatibility_registration_probe(
        lock, "0043_complete_publication_recovery_liabilities", monkeypatch
    )

    assert register_worker() == lock.pin

    assert len(parsed) == len(MIGRATION_REVISION_PATHS)
    assert database_reads == ["0043_complete_publication_recovery_liabilities"]
    assert registered == [lock.pin]


@pytest.mark.parametrize(
    "case",
    ("branch", "cycle", "ambiguous-head"),
    ids=("branch", "cycle", "ambiguous-head"),
)
def test_authenticated_migration_graph_rejects_branch_cycle_or_ambiguous_head(
    case, tmp_path, monkeypatch
):
    lock, _, _, _ = _write_test_lock(tmp_path, monkeypatch)
    if case == "branch":
        lock = _add_migration_source(
            lock,
            "0044_branch.py",
            {"revision": "0044_branch", "down_revision": "0042_publication_recovery_holds"},
        )
    elif case == "cycle":
        lock = _add_migration_source(
            lock,
            "0044_cycle_a.py",
            {"revision": "0044_cycle_a", "down_revision": "0045_cycle_b"},
        )
        lock = _add_migration_source(
            lock,
            "0045_cycle_b.py",
            {"revision": "0045_cycle_b", "down_revision": "0044_cycle_a"},
        )
    else:
        lock = _add_migration_source(
            lock,
            "0099_second_root.py",
            {"revision": "0099_second_root", "down_revision": None},
        )
    register_worker, database_reads, registered = _compatibility_registration_probe(
        lock, "0043_complete_publication_recovery_liabilities", monkeypatch
    )

    with pytest.raises(PublicationWorkerCompatibilityError):
        register_worker()

    assert database_reads == []
    assert registered == []


@pytest.mark.parametrize(
    "source",
    (
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "if False:\n    revision = 'ignored-but-still-a-write'\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "revision += '_rewritten'\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "if (revision := dynamic_revision()):\n    pass\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "[revision], untouched = ['rewritten'], None\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def rewrite():\n    down_revision: str = dynamic_parent()\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "if should_rewrite():\n    revision = dynamic_revision()\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "globals()['revision'] = dynamic_revision()\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "locals().update(revision=dynamic_revision())\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "locals().update({'revision': dynamic_revision()})\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "globals().update(down_revision=dynamic_parent())\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "globals().update({'down_revision': dynamic_parent()})\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "setattr(module, 'revision', dynamic_revision())\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "import sys\nsetattr(sys.modules[__name__], 'down_revision', dynamic_parent())\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "module.__dict__.update(revision=dynamic_revision())\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "sys.modules[__name__].__dict__.update({'down_revision': dynamic_parent()})\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "module.__dict__.update(**{'revision': dynamic_revision()})\n",
        "revision: dynamic_annotation() = '0043_floor'\n"
        "down_revision = '0042_parent'\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def upgrade():\n    setattr(module, 'revision', dynamic_revision())\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def upgrade():\n    globals().update(down_revision=dynamic_parent())\n",
    ),
    ids=(
        "literal-then-conditional-rewrite",
        "augmented-rewrite",
        "named-expression-rewrite",
        "destructuring-rewrite",
        "nested-annotated-rewrite",
        "dynamic-rewrite",
        "dynamic-global-rewrite",
        "locals-keyword-update",
        "locals-dict-update",
        "globals-keyword-update",
        "globals-dict-update",
        "setattr-module-rewrite",
        "setattr-sys-modules-rewrite",
        "module-dict-update",
        "sys-modules-dict-update",
        "module-dict-expanded-update",
        "unsupported-annotated-top-level-metadata",
        "function-setattr-metadata-rewrite",
        "function-global-update-metadata-rewrite",
    ),
)
def test_migration_revision_metadata_rejects_every_unsupported_write(source):
    from salience.cycles import publication_recovery_compatibility as compatibility

    with pytest.raises(PublicationWorkerCompatibilityError):
        compatibility._parse_revision_metadata("synthetic_revision.py", source.encode())


@pytest.mark.parametrize(
    "source",
    (
        "import builtins\n"
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def upgrade(*args: builtins.setattr(module, 'revision', dynamic_revision())):\n    pass\n",
        "import builtins\n"
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def upgrade(**kwargs: builtins.setattr(module, 'down_revision', dynamic_parent())):\n    pass\n",
        "import builtins\n"
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "@builtins.setattr(module, 'revision', dynamic_revision())\n"
        "def upgrade():\n    pass\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def upgrade(value=dynamic_metadata()):\n    pass\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def upgrade(*, value=dynamic_metadata()):\n    pass\n",
        "import builtins\n"
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def upgrade() -> builtins.setattr(module, 'revision', dynamic_revision()):\n    pass\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "from untrusted_module import *\n",
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "def upgrade[T]():\n    pass\n",
    ),
    ids=(
        "vararg-call-annotation",
        "kwarg-call-annotation",
        "decorator-namespace-mutation",
        "positional-default-mutation",
        "keyword-default-mutation",
        "return-call-annotation",
        "wildcard-import",
        "function-type-parameters",
    ),
)
def test_migration_import_surface_rejects_executable_function_declarations(source):
    from salience.cycles import publication_recovery_compatibility as compatibility

    with pytest.raises(PublicationWorkerCompatibilityError):
        compatibility._parse_revision_metadata("synthetic_revision.py", source.encode())


def test_migration_import_surface_accepts_current_inert_imports_and_annotations():
    from salience.cycles import publication_recovery_compatibility as compatibility

    source = (
        "from alembic import op\n"
        "from sqlalchemy import text\n"
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        "items: list[str] = []\n"
        "def upgrade(value: list[str]) -> None:\n    pass\n"
    )

    assert compatibility._parse_revision_metadata("synthetic_revision.py", source.encode()) == (
        "0043_floor",
        ("0042_parent",),
    )


@pytest.mark.parametrize(
    "annotation",
    (
        "descriptor.Type",
        "generic.Type[str]",
        "left.Type | right.Type",
    ),
    ids=("descriptor-getattr", "custom-class-getitem", "custom-union-or"),
)
def test_migration_import_surface_rejects_annotations_that_dispatch_user_code(annotation):
    """Reject AST forms that can invoke user-defined attribute or operator hooks."""
    from salience.cycles import publication_recovery_compatibility as compatibility

    source = (
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        f"def upgrade(value: {annotation}):\n    pass\n"
    )

    with pytest.raises(PublicationWorkerCompatibilityError):
        compatibility._parse_revision_metadata("synthetic_revision.py", source.encode())


@pytest.mark.parametrize(
    "import_statement",
    (
        "import malicious_helper",
        "from malicious_helper import rewrite_revision",
        "from . import malicious_helper",
        "from importlib import import_module",
    ),
    ids=("module-side-effect", "imported-rewriter", "relative-import", "dynamic-import-helper"),
)
def test_migration_import_surface_rejects_imports_that_can_rewrite_metadata(import_statement):
    from salience.cycles import publication_recovery_compatibility as compatibility

    source = (
        "# The imported module may rewrite this module's metadata during initialization.\n"
        f"{import_statement}\n"
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
    )

    with pytest.raises(PublicationWorkerCompatibilityError):
        compatibility._parse_revision_metadata("synthetic_revision.py", source.encode())


@pytest.mark.parametrize("shadowed_name", ("list", "str"), ids=("list", "str"))
def test_migration_import_surface_rejects_shadowing_safe_annotation_builtins(shadowed_name):
    from salience.cycles import publication_recovery_compatibility as compatibility

    value = "[]" if shadowed_name == "list" else "'shadowed'"
    annotation = "list[str]" if shadowed_name == "list" else "str"
    source = (
        f"{shadowed_name} = {value}\n"
        "revision = '0043_floor'\ndown_revision = '0042_parent'\n"
        f"def upgrade(value: {annotation}):\n    pass\n"
    )

    with pytest.raises(PublicationWorkerCompatibilityError):
        compatibility._parse_revision_metadata("synthetic_revision.py", source.encode())


def test_every_current_numbered_migration_has_safe_import_surface():
    from salience.cycles import publication_recovery_compatibility as compatibility

    migration_dir = ROOT / "migrations/versions"
    migration_paths = sorted(
        path for path in migration_dir.glob("[0-9][0-9][0-9][0-9]_*.py")
    )
    assert [int(path.name[:4]) for path in migration_paths] == list(range(1, 44))

    parsed = [
        compatibility._parse_revision_metadata(path.relative_to(ROOT).as_posix(), path.read_bytes())
        for path in migration_paths
    ]

    assert len({revision for revision, _parents in parsed}) == 43


def _synthetic_builder_inputs(tmp_path, *, extra_migrations=()):
    repo_root = tmp_path / "candidate-repository"
    runtime_root = tmp_path / "runtime-root"
    repo_root.mkdir()
    runtime_root.mkdir()
    source_data = {}
    for source_name in SOURCE_PATHS:
        data = (ROOT / source_name).read_bytes()
        source_data[source_name] = data
        target = repo_root / source_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for filename, metadata in extra_migrations:
        source_name = f"migrations/versions/{filename}"
        data = (
            f"revision = {metadata['revision']!r}\n"
            f"down_revision = {metadata['down_revision']!r}\n"
        ).encode()
        source_data[source_name] = data
        target = repo_root / source_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test Builder"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "builder@example.invalid"], cwd=repo_root, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "synthetic candidate"], cwd=repo_root, check=True)
    application_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    installed_files = []
    for source_name, data in source_data.items():
        if source_name == "scripts/launch-v4-publication-worker.py":
            continue
        installed_name = _installed_path(source_name)
        target = runtime_root / installed_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if source_name.startswith("src/"):
            installed_files.append((installed_name, data))

    dist_info = runtime_root / "salience-0.1.0.dist-info"
    dist_info.mkdir()
    record_path = dist_info / "RECORD"
    rows = [
        (name, "sha256=" + _record_digest(data), str(len(data)))
        for name, data in installed_files
    ]
    record_relative = str(record_path.relative_to(runtime_root))
    rows.append((record_relative, "", ""))
    with record_path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(rows)
    record_data = record_path.read_bytes()

    wheel_path = tmp_path / "salience-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in installed_files:
            archive.writestr(name, data)
        archive.writestr(record_relative, record_data)
    return repo_root, application_commit, wheel_path, runtime_root


def test_publication_worker_launch_lock_builder_emits_canonical_lock_digest_and_identical_control_bundle(
    tmp_path, monkeypatch
):
    builder = _lock_builder_module()
    repo_root, application_commit, wheel_path, runtime_root = _synthetic_builder_inputs(tmp_path)
    monkeypatch.setattr(builder, "REPOSITORY_ROOT", repo_root)
    monkeypatch.setattr(builder, "TARGET_RUNTIME_ROOT", "/usr/local/lib/python3.13/site-packages")
    monkeypatch.setattr(builder, "TARGET_INTERPRETER_PATH", sys.executable)
    lock_path = tmp_path / "deployment/publication-worker-launch-lock.json"
    independent_digest_path = tmp_path / "artifacts/publication-worker-launch-lock.sha256"
    control_dir = tmp_path / "external-control"

    lock = builder.build_publication_worker_launch_lock(
        application_commit=application_commit,
        wheel=wheel_path,
        runtime_root=runtime_root,
        lock_path=lock_path,
        independent_digest_path=independent_digest_path,
        control_dir=control_dir,
    )

    lock_bytes = lock_path.read_bytes()
    expected_lock_bytes = (
        json.dumps(asdict(lock), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    expected_digest = _sha256(lock_bytes) + "\n"
    assert lock_bytes == expected_lock_bytes
    assert independent_digest_path.read_bytes() == expected_digest.encode("ascii")
    assert (control_dir / "publication-worker-launch-lock.json").read_bytes() == lock_bytes
    assert (control_dir / "launch-v4-publication-worker.py").read_bytes() == (
        repo_root / "scripts/launch-v4-publication-worker.py"
    ).read_bytes()
    assert (control_dir / "publication-worker-launch-lock.sha256").read_bytes() == (
        independent_digest_path.read_bytes()
    )


def test_publication_worker_launch_lock_builder_pins_explicit_interpreter_source_without_changing_target_path(
    tmp_path, monkeypatch
):
    builder = _lock_builder_module()
    repo_root, application_commit, wheel_path, runtime_root = _synthetic_builder_inputs(tmp_path)
    monkeypatch.setattr(builder, "REPOSITORY_ROOT", repo_root)
    monkeypatch.setattr(builder, "TARGET_RUNTIME_ROOT", "/usr/local/lib/python3.13/site-packages")
    monkeypatch.setattr(builder, "TARGET_INTERPRETER_PATH", "/usr/local/bin/python")
    interpreter_source = tmp_path / "image-prefix/usr/local/bin/python"
    interpreter_source.parent.mkdir(parents=True)
    interpreter_bytes = b"exact target-image interpreter bytes\n"
    interpreter_source.write_bytes(interpreter_bytes)
    lock_path, digest_path, control_dir = _builder_output_paths(tmp_path)

    lock = builder.build_publication_worker_launch_lock(
        application_commit=application_commit,
        wheel=wheel_path,
        runtime_root=runtime_root,
        lock_path=lock_path,
        independent_digest_path=digest_path,
        control_dir=control_dir,
        interpreter_source=interpreter_source,
    )

    assert lock.interpreter_path == "/usr/local/bin/python"
    assert lock.interpreter_sha256 == _sha256(interpreter_bytes)


def _builder_output_paths(tmp_path):
    return (
        tmp_path / "deployment/publication-worker-launch-lock.json",
        tmp_path / "artifacts/publication-worker-launch-lock.sha256",
        tmp_path / "external-control",
    )


def _invoke_synthetic_builder(builder, tmp_path, application_commit, wheel_path, runtime_root):
    lock_path, digest_path, control_dir = _builder_output_paths(tmp_path)
    return builder.build_publication_worker_launch_lock(
        application_commit=application_commit,
        wheel=wheel_path,
        runtime_root=runtime_root,
        lock_path=lock_path,
        independent_digest_path=digest_path,
        control_dir=control_dir,
    )


def test_publication_worker_launch_lock_builder_accepts_valid_wheel_and_installed_record(
    tmp_path, monkeypatch
):
    builder = _lock_builder_module()
    repo_root, application_commit, wheel_path, runtime_root = _synthetic_builder_inputs(tmp_path)
    monkeypatch.setattr(builder, "REPOSITORY_ROOT", repo_root)
    monkeypatch.setattr(builder, "TARGET_RUNTIME_ROOT", "/usr/local/lib/python3.13/site-packages")
    monkeypatch.setattr(builder, "TARGET_INTERPRETER_PATH", sys.executable)

    lock = _invoke_synthetic_builder(
        builder, tmp_path, application_commit, wheel_path, runtime_root
    )

    record_path = runtime_root / "salience-0.1.0.dist-info/RECORD"
    assert lock.pin.distribution_record_sha256 == _sha256(record_path.read_bytes())


def test_publication_worker_launch_lock_builder_accepts_real_synthetic_0044_descendant(
    tmp_path, monkeypatch
):
    builder = _lock_builder_module()
    repo_root, application_commit, wheel_path, runtime_root = _synthetic_builder_inputs(
        tmp_path,
        extra_migrations=(
            (
                "0044_qualified_builder_descendant.py",
                {
                    "revision": "0044_qualified_builder_descendant",
                    "down_revision": "0043_complete_publication_recovery_liabilities",
                },
            ),
        ),
    )
    monkeypatch.setattr(builder, "REPOSITORY_ROOT", repo_root)
    monkeypatch.setattr(builder, "TARGET_RUNTIME_ROOT", "/usr/local/lib/python3.13/site-packages")
    monkeypatch.setattr(builder, "TARGET_INTERPRETER_PATH", sys.executable)

    lock = _invoke_synthetic_builder(
        builder, tmp_path, application_commit, wheel_path, runtime_root
    )

    assert lock.pin.application_commit == application_commit
    assert any(
        item.path == "migrations/versions/0044_qualified_builder_descendant.py"
        for item in lock.pin.source_files
    )


@pytest.mark.parametrize(
    "case",
    (
        "application-commit-head-mismatch",
        "dirty-tracked-worktree",
        "runtime-inventory-mismatch",
        "wheel-source-mismatch",
        "wheel-record-mismatch",
        "installed-record-wrong-hash",
        "installed-record-wrong-size",
    ),
    ids=(
        "application-commit-head-mismatch",
        "dirty-tracked-worktree",
        "runtime-inventory-mismatch",
        "wheel-source-mismatch",
        "wheel-record-mismatch",
        "installed-record-wrong-hash",
        "installed-record-wrong-size",
    ),
)
def test_publication_worker_launch_lock_builder_rejects_untrusted_candidate_state(
    case, tmp_path, monkeypatch
):
    builder = _lock_builder_module()
    repo_root, application_commit, wheel_path, runtime_root = _synthetic_builder_inputs(tmp_path)
    monkeypatch.setattr(builder, "REPOSITORY_ROOT", repo_root)
    monkeypatch.setattr(builder, "TARGET_RUNTIME_ROOT", "/usr/local/lib/python3.13/site-packages")
    monkeypatch.setattr(builder, "TARGET_INTERPRETER_PATH", sys.executable)

    if case == "application-commit-head-mismatch":
        subprocess.run(["git", "commit", "--allow-empty", "-m", "advance HEAD"], cwd=repo_root, check=True)
    elif case == "dirty-tracked-worktree":
        tracked_path = repo_root / "migrations/env.py"
        tracked_path.write_bytes(tracked_path.read_bytes() + b"\n# dirty\n")
    elif case == "runtime-inventory-mismatch":
        extra_path = runtime_root / "migrations/versions/0044_unpinned.py"
        extra_path.write_text(
            "revision = '0044_unpinned'\ndown_revision = '0043_complete_publication_recovery_liabilities'\n",
            encoding="utf-8",
        )
    elif case == "wheel-source-mismatch":
        replacement = tmp_path / "changed-wheel-member.whl"
        with zipfile.ZipFile(wheel_path) as source, zipfile.ZipFile(replacement, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "salience/cycles/publication_recovery_compatibility.py":
                    data += b"\n# changed wheel source\n"
                target.writestr(name, data)
        replacement.replace(wheel_path)
    elif case == "wheel-record-mismatch":
        replacement = tmp_path / "changed-wheel-record.whl"
        with zipfile.ZipFile(wheel_path) as source, zipfile.ZipFile(replacement, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name.endswith(".dist-info/RECORD"):
                    rows = list(csv.reader(io.StringIO(data.decode())))
                    rows[0][1] = "sha256=" + ("A" if rows[0][1][7] != "A" else "B") + rows[0][1][8:]
                    stream = io.StringIO(newline="")
                    csv.writer(stream, lineterminator="\n").writerows(rows)
                    data = stream.getvalue().encode()
                target.writestr(name, data)
        replacement.replace(wheel_path)
    elif case in {"installed-record-wrong-hash", "installed-record-wrong-size"}:
        record_path = next(runtime_root.glob("*.dist-info/RECORD"))
        rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
        package_row = next(row for row in rows if row[0] == "salience/cycles/publication_recovery_compatibility.py")
        if case == "installed-record-wrong-hash":
            digest = package_row[1]
            package_row[1] = digest[:-1] + ("A" if digest[-1] != "A" else "B")
        else:
            package_row[2] = str(int(package_row[2]) + 1)
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator="\n").writerows(rows)
        record_path.write_text(stream.getvalue(), encoding="utf-8")

    lock_path, digest_path, control_dir = _builder_output_paths(tmp_path)
    with pytest.raises(ValueError):
        builder.build_publication_worker_launch_lock(
            application_commit=application_commit,
            wheel=wheel_path,
            runtime_root=runtime_root,
            lock_path=lock_path,
            independent_digest_path=digest_path,
            control_dir=control_dir,
        )

    assert not lock_path.exists()
    assert not digest_path.exists()
    assert not control_dir.exists()


def test_publication_worker_launch_lock_builder_failure_preserves_published_bundle(
    tmp_path, monkeypatch
):
    builder = _lock_builder_module()
    repo_root, application_commit, wheel_path, runtime_root = _synthetic_builder_inputs(tmp_path)
    monkeypatch.setattr(builder, "REPOSITORY_ROOT", repo_root)
    monkeypatch.setattr(builder, "TARGET_RUNTIME_ROOT", "/usr/local/lib/python3.13/site-packages")
    monkeypatch.setattr(builder, "TARGET_INTERPRETER_PATH", sys.executable)
    lock_path, digest_path, control_dir = _builder_output_paths(tmp_path)
    control_dir.mkdir(parents=True)
    outputs = {
        lock_path: b"old tracked lock\n",
        digest_path: b"old independent digest\n",
        control_dir / "publication-worker-launch-lock.json": b"old control lock\n",
        control_dir / "publication-worker-launch-lock.sha256": b"old control digest\n",
        control_dir / "launch-v4-publication-worker.py": b"old control launcher\n",
    }
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    original_replace = builder.os.replace
    replacements = []

    def fail_during_publish(source, destination):
        replacements.append((Path(source), Path(destination)))
        if len(replacements) == 3:
            raise OSError("injected publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(builder.os, "replace", fail_during_publish)
    with pytest.raises(OSError, match="injected publication failure"):
        builder.build_publication_worker_launch_lock(
            application_commit=application_commit,
            wheel=wheel_path,
            runtime_root=runtime_root,
            lock_path=lock_path,
            independent_digest_path=digest_path,
            control_dir=control_dir,
        )

    assert len(replacements) >= 5
    assert {path: path.read_bytes() for path in outputs} == outputs


def test_publication_worker_launch_lock_builder_preserves_recovery_bundle_on_restore_failure(
    tmp_path, monkeypatch
):
    builder = _lock_builder_module()
    repo_root, application_commit, wheel_path, runtime_root = _synthetic_builder_inputs(tmp_path)
    monkeypatch.setattr(builder, "REPOSITORY_ROOT", repo_root)
    monkeypatch.setattr(builder, "TARGET_RUNTIME_ROOT", "/usr/local/lib/python3.13/site-packages")
    monkeypatch.setattr(builder, "TARGET_INTERPRETER_PATH", sys.executable)
    lock_path, digest_path, control_dir = _builder_output_paths(tmp_path)
    control_dir.mkdir(parents=True)
    outputs = {
        lock_path: b"old tracked lock\n",
        digest_path: b"old independent digest\n",
        control_dir / "publication-worker-launch-lock.json": b"old control lock\n",
        control_dir / "publication-worker-launch-lock.sha256": b"old control digest\n",
        control_dir / "launch-v4-publication-worker.py": b"old control launcher\n",
    }
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    original_replace = builder.os.replace
    replacements = []

    def fail_publish_and_restore(source, destination):
        replacements.append((Path(source), Path(destination)))
        if len(replacements) == 3:
            raise OSError("injected publication failure")
        if len(replacements) == 4:
            raise OSError("injected restoration failure")
        return original_replace(source, destination)

    monkeypatch.setattr(builder.os, "replace", fail_publish_and_restore)
    with pytest.raises(RuntimeError, match="recovery artifacts preserved") as failure:
        builder.build_publication_worker_launch_lock(
            application_commit=application_commit,
            wheel=wheel_path,
            runtime_root=runtime_root,
            lock_path=lock_path,
            independent_digest_path=digest_path,
            control_dir=control_dir,
        )

    backups = list(tmp_path.rglob(".publication-worker-launch-lock-stage-*/output-*.prior"))
    backup_by_index = {int(path.name.split("-")[1].split(".")[0]): path for path in backups}
    assert set(backup_by_index) == set(range(len(outputs)))
    assert [backup_by_index[index].read_bytes() for index in range(len(outputs))] == list(outputs.values())
    assert all(str(path) in str(failure.value) for path in backups)
    assert all(str(path.parent) in str(failure.value) for path in backups)
    manifests = list(tmp_path.rglob(".publication-worker-launch-lock-stage-*/recovery-manifest.json"))
    assert manifests
    recovery_entries = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert {entry["target"] for entry in recovery_entries} == {str(path) for path in outputs}
    assert all(
        Path(entry["backup"]).read_bytes() == outputs[Path(entry["target"])]
        for entry in recovery_entries
    )
    assert all(str(path) in str(failure.value) for path in manifests)


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
    monkeypatch.setenv("DATABASE_URL", "postgresql://checked/deployable_a")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://worker/unrelated_b")
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
            [
                lock.interpreter_path,
                "-I",
                "-B",
                "-S",
                "-c",
                launcher._WORKER_BOOTSTRAP,
                str(runtime_root),
                "salience.workflows.worker",
            ],
        )
    ]
    assert working_directories == [runtime_root]
    assert "PYTHONPATH" not in environments[0] and "PYTHONHOME" not in environments[0]
    assert environments[0]["SALIENCE_PUBLICATION_WORKER_START_MODE"] == "current"
    assert environments[0]["SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256"] == digest
    assert environments[0]["DATABASE_URL"] == "postgresql://checked/deployable_a"
    assert environments[0]["TEST_DATABASE_URL"] == "postgresql://worker/unrelated_b"
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


@pytest.mark.parametrize("mode", ("current", "rollback-restart"))
def test_launcher_denies_divergent_database_urls_before_legacy_fixture_start(
    mode, tmp_path, monkeypatch
):
    launcher = _launcher_module()
    lock, wheel_path, _, _ = _write_test_lock(tmp_path, monkeypatch)
    _configure_launcher(launcher, tmp_path, lock, wheel_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://checked/verified_a")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://worker/fixture_b")
    preflights = []
    worker_starts = []
    monkeypatch.setattr(launcher.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: (
            preflights.append((args, kwargs))
            or type("Result", (), {"returncode": 0, "stderr": ""})()
        ),
    )
    monkeypatch.setattr(
        launcher.os,
        "execve",
        lambda *args: worker_starts.append(args),
    )

    arguments = ["--mode", mode, "--worker-kind", "legacy-fixture"]
    if mode == "rollback-restart":
        arguments.extend(
            [
                "--workspace-id",
                "95416086-6229-460e-8007-90a20873d1a2",
                "--expired-hold-operation-id",
                "4000f343-7f68-4e03-8574-65087f465132",
                "--cancel-hold-operation-id",
                "06b54014-c574-4c6d-9409-0daf78767071",
            ]
        )
    with pytest.raises(launcher.LauncherError, match="DATABASE_URL"):
        launcher.main(arguments)

    assert preflights == []
    assert worker_starts == []


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


def test_record_verifiers_hash_external_script_rows_within_interpreter_prefix(
    tmp_path, monkeypatch
):
    from salience.cycles import publication_recovery_compatibility as compatibility

    lock, _, runtime_root, _ = _write_test_lock(
        tmp_path, monkeypatch, include_interpreter_prefix_record=True
    )
    launcher = _launcher_module()
    monkeypatch.setattr(launcher, "FIXED_INTERPRETER_PATH", lock.interpreter_path)
    installation_prefix = Path(lock.interpreter_path).absolute().parent.parent.resolve()
    record_path = Path(lock.pin.distribution_record_path)
    rows = list(csv.reader(record_path.read_text(encoding="utf-8").splitlines()))
    external_row = next(
        row for row in rows if (runtime_root / row[0]).resolve() == installation_prefix / "bin/content"
    )
    assert external_row[0].startswith("..")

    compatibility_recorded = compatibility._validate_record(lock, runtime_root)
    assert external_row[0] in compatibility_recorded
    launcher_recorded = launcher._verify_record(asdict(lock.pin), runtime_root)
    assert external_row[0] in launcher_recorded

    def rewrite_record(updated_rows):
        with record_path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, lineterminator="\n").writerows(updated_rows)
        data = record_path.read_bytes()
        return replace(
            lock,
            pin=replace(lock.pin, distribution_record_sha256=_sha256(data)),
        )

    alias = runtime_root / "venv-config-alias"
    alias.symlink_to(installation_prefix / "bin/content")
    alias_rows = rows + [
        ["venv-config-alias", external_row[1], external_row[2]],
    ]
    duplicate_lock = rewrite_record(alias_rows)
    with pytest.raises(PublicationWorkerCompatibilityError, match="repeat|duplicate"):
        compatibility._validate_record(duplicate_lock, runtime_root)
    with pytest.raises(launcher.LauncherError, match="duplicate"):
        launcher._verify_record(asdict(duplicate_lock.pin), runtime_root)

    escaping = runtime_root / "outside-prefix"
    escaping.symlink_to(Path("/etc/passwd"))
    escape_content = Path("/etc/passwd").read_bytes()
    escape_rows = rows + [
        ["outside-prefix", "sha256=" + _record_digest(escape_content), str(len(escape_content))],
    ]
    escape_lock = rewrite_record(escape_rows)
    with pytest.raises(PublicationWorkerCompatibilityError, match="prefix|escapes"):
        compatibility._validate_record(escape_lock, runtime_root)
    with pytest.raises(launcher.LauncherError, match="prefix|escapes"):
        launcher._verify_record(asdict(escape_lock.pin), runtime_root)

    for encoded_hash, size in (("", ""), (external_row[1], ""), ("", external_row[2])):
        incomplete_rows = list(rows)
        index = incomplete_rows.index(external_row)
        incomplete_rows[index] = [external_row[0], encoded_hash, size]
        incomplete_lock = rewrite_record(incomplete_rows)
        with pytest.raises(PublicationWorkerCompatibilityError, match="hash|size"):
            compatibility._validate_record(incomplete_lock, runtime_root)
        with pytest.raises(launcher.LauncherError, match="hash|size"):
            launcher._verify_record(asdict(incomplete_lock.pin), runtime_root)


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

    dockerignore_lines = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {".git", ".venv", "**/__pycache__", "**/*.py[cod]"} <= dockerignore_lines
    assert not any(
        line == "artifacts" or line.startswith("artifacts/") or line == "**/artifacts"
        for line in dockerignore_lines
    )

    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert "ARG SALIENCE_WHEEL_FILE" in dockerfile
    assert "/opt/salience-artifacts/salience.whl" in dockerfile
    pip_wheel_alias = "/opt/salience-artifacts/salience-0.1.0-py3-none-any.whl"
    assert f"ln -s /opt/salience-artifacts/salience.whl {pip_wheel_alias}" in dockerfile
    assert f"python -m pip install --no-cache-dir --no-compile {pip_wheel_alias}" in dockerfile
    assert "pip install ." not in dockerfile
    command_line = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    docker_command = json.loads(command_line.removeprefix("CMD "))
    expected_command = [
        "/usr/local/bin/python",
        "-I",
        "-B",
        "-S",
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


def test_dockerfile_and_compose_worker_command_use_isolated_launcher_flags():
    import yaml

    expected = [
        "/usr/local/bin/python",
        "-I",
        "-B",
        "-S",
        "/run/salience-control/launch-v4-publication-worker.py",
        "--mode",
        "current",
        "--worker-kind",
        "deployable",
    ]
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    command_line = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    assert json.loads(command_line.removeprefix("CMD ")) == expected
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    assert compose["services"]["worker"]["command"] == expected


def test_compose_worker_mounts_generated_candidate_control_bundle_read_only():
    import yaml

    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]
    expected = {
        "PUBLICATION_WORKER_LAUNCHER_PATH": (
            "./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/launch-v4-publication-worker.py",
            "/run/salience-control/launch-v4-publication-worker.py",
        ),
        "PUBLICATION_WORKER_LOCK_PATH": (
            "./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.json",
            "/run/salience-control/publication-worker-launch-lock.json",
        ),
        "PUBLICATION_WORKER_LOCK_DIGEST_PATH": (
            "./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.sha256",
            "/run/salience-control/publication-worker-launch-lock.sha256",
        ),
    }
    assert worker["read_only"] is True
    assert len(worker["volumes"]) == len(expected)
    observed = {}
    for volume in worker["volumes"]:
        variable, default = volume.split(":-", 1)
        variable = variable.removeprefix("${").removesuffix("}")
        source, destination, mode = default.rsplit(":", 2)
        source = source.removesuffix("}")
        assert mode == "ro"
        observed[variable] = (source, destination)
    assert observed == expected


def _load_worker_harness():
    path = ROOT / "tests/fixtures/v4_publication_floor_worker.py"
    spec = importlib.util.spec_from_file_location("v4_publication_floor_worker", path)
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


def test_start_rollback_worker_argv_uses_isolated_launcher_flags(tmp_path, monkeypatch):
    harness = _load_worker_harness()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    monkeypatch.setattr(harness, "image_id", lambda _image: "sha256:test-image")
    bundle = tmp_path / "control-bundle"
    bundle.mkdir()
    result = harness.start_rollback_worker(
        image="synthetic-no-effects-worker:fixture",
        database_url="postgresql://no-effects.invalid/fixture",
        temporal_target="127.0.0.1:1",
        workspace_id="00000000-0000-0000-0000-000000000001",
        expired_operation_id="00000000-0000-0000-0000-000000000002",
        cancel_operation_id="00000000-0000-0000-0000-000000000003",
        task_queue="no-effects-fixture",
        control_bundle=bundle,
        name="task4-argv-test",
    )
    command = calls[0]
    image_index = command.index("synthetic-no-effects-worker:fixture")
    assert command.index("--read-only") < image_index
    expected_argv = [
        "/usr/local/bin/python",
        "-I",
        "-B",
        "-S",
        "/run/salience-control/launch-v4-publication-worker.py",
        "--mode",
        "rollback-restart",
        "--worker-kind",
        "legacy-fixture",
        "--workspace-id",
        "00000000-0000-0000-0000-000000000001",
        "--expired-hold-operation-id",
        "00000000-0000-0000-0000-000000000002",
        "--cancel-hold-operation-id",
        "00000000-0000-0000-0000-000000000003",
    ]
    assert command[image_index + 1 :] == expected_argv
    assert json.loads(result["launcher_argv"]) == expected_argv
    mounts = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "--mount"]
    assert mounts == [
        f"type=bind,source={(bundle / 'launch-v4-publication-worker.py').resolve()},target=/run/salience-control/launch-v4-publication-worker.py,readonly",
        f"type=bind,source={(bundle / 'publication-worker-launch-lock.json').resolve()},target=/run/salience-control/publication-worker-launch-lock.json,readonly",
        f"type=bind,source={(bundle / 'publication-worker-launch-lock.sha256').resolve()},target=/run/salience-control/publication-worker-launch-lock.sha256,readonly",
    ]


def test_start_rollback_worker_installs_recorder_on_the_actual_adapter_path(
    tmp_path, monkeypatch
):
    from uuid import uuid4

    harness = _load_worker_harness()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    monkeypatch.setattr(harness, "image_id", lambda _image: "sha256:test-image")
    bundle = tmp_path / "control-bundle"
    bundle.mkdir()
    recorder_url = "http://adapter-recorder:8081"
    run_id = str(uuid4())
    worker_id = str(uuid4())
    harness.start_rollback_worker(
        image="synthetic-no-effects-worker:fixture",
        database_url="postgresql://no-effects.invalid/fixture",
        temporal_target="127.0.0.1:1",
        workspace_id="00000000-0000-0000-0000-000000000001",
        expired_operation_id="00000000-0000-0000-0000-000000000002",
        cancel_operation_id="00000000-0000-0000-0000-000000000003",
        task_queue="no-effects-fixture",
        control_bundle=bundle,
        name="task7-instrumented-launch-test",
        adapter_recorder_url=recorder_url,
        adapter_recorder_run_id=run_id,
        adapter_recorder_worker_id=worker_id,
    )

    command = calls[0]
    command_pairs = tuple(zip(command, command[1:]))
    assert ("--env", f"TASK7_ADAPTER_RECORDER_URL={recorder_url}") in command_pairs
    assert f"TASK7_ADAPTER_RECORDER_RUN_ID={run_id}" in command
    assert f"TASK7_ADAPTER_RECORDER_WORKER_ID={worker_id}" in command
    recorder_mount = next(
        item
        for item in command
        if item.endswith(
            "target=/run/task7-adapter-operation-recorder.py,readonly"
        )
    )
    assert recorder_mount.startswith("type=bind,source=")

    image_index = command.index("synthetic-no-effects-worker:fixture")
    worker_argv = command[image_index + 1 :]
    assert worker_argv[:5] == ["/usr/local/bin/python", "-I", "-B", "-S", "-c"]
    assert "install_publication_activities_adapter_recorder" in worker_argv[5]
    assert worker_argv[6] == harness.CONTROL_LAUNCHER
    assert worker_argv[7:9] == ["--mode", "rollback-restart"]


def test_adapter_recorder_launch_shim_wraps_authenticated_launcher_exec(monkeypatch):
    import os
    import runpy
    import sys

    harness = _load_worker_harness()
    run_id = "00000000-0000-0000-0000-000000000011"
    worker_id = "00000000-0000-0000-0000-000000000012"
    environment = {
        "TASK7_ADAPTER_RECORDER_URL": "http://adapter-recorder:8081",
        "TASK7_ADAPTER_RECORDER_RUN_ID": run_id,
        "TASK7_ADAPTER_RECORDER_WORKER_ID": worker_id,
    }
    captured = {}

    def fake_execve(path, argv, child_environment):
        captured["path"] = path
        captured["argv"] = list(argv)
        captured["environment"] = child_environment
        return None

    def fake_run_path(path, *, run_name):
        assert path == harness.CONTROL_LAUNCHER
        assert run_name == "__main__"
        worker_bootstrap = "import runpy, sys\nruntime_root, module, *module_args = sys.argv[1:]\n"
        command = [
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            "-c",
            worker_bootstrap,
            "/usr/local/lib/python3.13/site-packages",
            "salience.workflows.worker",
        ]
        return os.execve(command[0], command, environment)

    monkeypatch.setattr(os, "execve", fake_execve)
    monkeypatch.setattr(runpy, "run_path", fake_run_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "-c",
            harness.CONTROL_LAUNCHER,
            "--mode",
            "rollback-restart",
        ],
    )
    exec(
        compile(harness._ADAPTER_RECORDER_LAUNCH_SHIM, "<task7-launch-shim>", "exec"),
        {"__name__": "__main__"},
    )

    child_code = captured["argv"][5]
    assert "install_publication_activities_adapter_recorder" in child_code
    assert "runtime_root, module, *module_args = sys.argv[1:]" in child_code
    assert captured["environment"] == environment


def test_publication_activities_recorder_installer_observes_the_real_adapter_lookup():
    from threading import Thread
    from types import SimpleNamespace
    from uuid import uuid4

    from salience.workflows.publication import PublicationActivities

    receiver = _load_adapter_operation_fixture(
        ROOT / "tests/fixtures/v4_publication_adapter_operation_receiver.py",
        "v4_publication_adapter_operation_receiver_for_worker_installer",
    )
    recorder = _load_adapter_operation_fixture(
        ROOT / "tests/fixtures/v4_publication_adapter_operation_recorder.py",
        "v4_publication_adapter_operation_recorder_for_worker_installer",
    )

    class FixtureAdapterWithDiagnostics(recorder._NeutralProbeAdapter):
        @property
        def submit_count(self):
            return 7

    server = receiver.create_server("127.0.0.1", 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    run_id = str(uuid4())
    worker_id = str(uuid4())
    restore = None
    try:
        restore = recorder.install_publication_activities_adapter_recorder(
            receiver_url=f"http://127.0.0.1:{server.server_port}",
            run_id=run_id,
            worker_id=worker_id,
        )
        activities = PublicationActivities(
            SimpleNamespace(
                provider=FixtureAdapterWithDiagnostics(),
                publisher_adapters={},
            )
        )
        adapter = activities._adapter_for(
            SimpleNamespace(publisher_id="operation-probe")
        )
        asyncio.run(adapter.preflight(object()))
        assert activities._state.provider.submit_count == 7
        events = _read_adapter_recorder_events(
            f"http://127.0.0.1:{server.server_port}", run_id
        )
        _assert_adapter_operation_events(
            events,
            run_id=run_id,
            operations=("capabilities", "preflight"),
        )
        assert {event["worker_id"] for event in events} == {worker_id}
    finally:
        if restore is not None:
            restore()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_invoke_launcher_argv_uses_isolated_launcher_flags(tmp_path, monkeypatch):
    harness = _load_worker_harness()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 78, stdout="", stderr="denied")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    bundle = tmp_path / "control-bundle"
    bundle.mkdir()
    result = harness.invoke_launcher(
        image="synthetic-no-effects-worker:fixture",
        database_url="postgresql://no-effects.invalid/fixture",
        control_bundle=bundle,
        argv=("--mode", "current", "--worker-kind", "legacy-fixture"),
    )
    assert result.returncode == 78
    command = calls[0]
    image_index = command.index("synthetic-no-effects-worker:fixture")
    assert command.index("--read-only") < image_index
    assert command[image_index + 1 :] == [
        "/usr/local/bin/python",
        "-I",
        "-B",
        "-S",
        "/run/salience-control/launch-v4-publication-worker.py",
        "--mode",
        "current",
        "--worker-kind",
        "legacy-fixture",
    ]


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (
        ("artifact-digest-mismatch", "retained publication worker wheel hash mismatch"),
        ("lock-digest-mismatch", "independent trust root"),
        ("launcher-hash-mismatch", "launcher source hash mismatch"),
        ("interpreter-hash-mismatch", "interpreter hash mismatch"),
        ("runtime-root-mismatch", "runtime root does not match the fixed root"),
    ),
    ids=(
        "artifact-digest-mismatch",
        "lock-digest-mismatch",
        "launcher-hash-mismatch",
        "interpreter-hash-mismatch",
        "runtime-root-mismatch",
    ),
)
def test_supported_launcher_authenticates_artifact_lock_interpreter_and_runtime_root_before_registration(
    case, expected_error, tmp_path, monkeypatch
):
    fixture = _write_local_launcher_process_fixture(tmp_path, monkeypatch)
    if case == "artifact-digest-mismatch":
        fixture["wheel"].write_bytes(fixture["wheel"].read_bytes() + b"tamper")
    elif case == "lock-digest-mismatch":
        fixture["lock_path"].write_bytes(fixture["lock_path"].read_bytes() + b" ")
    elif case == "launcher-hash-mismatch":
        fixture["launcher"].write_bytes(fixture["launcher"].read_bytes() + b"\n")
        changed_launcher = fixture["launcher"].read_bytes()

        def update_launcher_source_pin(lock):
            source_files = lock["pin"]["source_files"]
            source_pin = next(
                item for item in source_files
                if item["path"] == "scripts/launch-v4-publication-worker.py"
            )
            source_pin["git_blob"] = _git_blob(changed_launcher)
            source_pin["sha256"] = _sha256(changed_launcher)
            manifest = (json.dumps(source_files, sort_keys=True, separators=(",", ":")) + "\n").encode()
            lock["pin"]["source_manifest_sha256"] = _sha256(manifest)

        _rewrite_process_fixture_lock(fixture, update_launcher_source_pin)
    elif case == "interpreter-hash-mismatch":
        _rewrite_process_fixture_lock(
            fixture,
            lambda lock: lock.__setitem__("interpreter_sha256", "0" * 64),
        )
    elif case == "runtime-root-mismatch":
        (tmp_path / "untrusted-root").mkdir()
        _rewrite_process_fixture_lock(
            fixture,
            lambda lock: lock.__setitem__("runtime_root", str(tmp_path / "untrusted-root")),
        )
    result = _run_local_supported_launcher(fixture)
    assert result.returncode == 78, result.stderr
    assert expected_error in result.stderr
    assert not fixture["preflight_import_marker"].exists()
    assert not fixture["site_marker"].exists()
    assert not fixture["worker_marker"].exists()


@pytest.mark.parametrize(
    "case",
    (
        "hostile-pythonpath",
        "hostile-pythonhome-sitecustomize",
        "hostile-working-directory",
        "valid-pyc",
    ),
    ids=(
        "hostile-pythonpath",
        "hostile-pythonhome-sitecustomize",
        "hostile-working-directory",
        "valid-pyc",
    ),
)
def test_supported_launcher_rejects_hostile_startup_and_pyc_before_registration(
    case, tmp_path, monkeypatch
):
    fixture = _write_local_launcher_process_fixture(tmp_path, monkeypatch)
    hostile = tmp_path / "hostile-startup"
    hostile.mkdir()
    hostile_marker = tmp_path / "hostile-module-imported"
    (hostile / "sitecustomize.py").write_text(
        f"from pathlib import Path; Path({str(hostile_marker)!r}).write_text('hostile')\n",
        encoding="utf-8",
    )
    (hostile / "salience.py").write_text(
        f"from pathlib import Path; Path({str(hostile_marker)!r}).write_text('shadow')\n",
        encoding="utf-8",
    )
    child_cwd = fixture["runtime_root"]
    extra_environment = {}
    if case == "hostile-pythonpath":
        extra_environment["PYTHONPATH"] = str(hostile)
    elif case == "hostile-pythonhome-sitecustomize":
        extra_environment["PYTHONHOME"] = str(hostile)
        extra_environment["PYTHONPATH"] = str(hostile)
    elif case == "hostile-working-directory":
        child_cwd = hostile
    else:
        source = fixture["runtime_root"] / "salience/cycles/publication_recovery_compatibility.py"
        cache_path = Path(importlib.util.cache_from_source(str(source)))
        cache_path.parent.mkdir(parents=True)
        malicious = compile(
            f"from pathlib import Path; Path({str(hostile_marker)!r}).write_text('cached')",
            str(source),
            "exec",
        )
        header = importlib.util.MAGIC_NUMBER + struct.pack(
            "<III", 0, int(source.stat().st_mtime), source.stat().st_size
        )
        cache_path.write_bytes(header + marshal.dumps(malicious))
        positive_control = subprocess.run(
            [
                str(fixture["interpreter"]),
                "-I",
                "-B",
                "-S",
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import salience.cycles.publication_recovery_compatibility",
                str(fixture["runtime_root"]),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert positive_control.returncode == 0, positive_control.stderr
        assert hostile_marker.read_text(encoding="utf-8") == "cached"
        hostile_marker.unlink()
    result = _run_local_supported_launcher(
        fixture, cwd=child_cwd, extra_environment=extra_environment
    )
    assert result.returncode == 78, result.stderr
    assert "synthetic preflight denial" in result.stderr or ".pyc" in result.stderr
    assert not fixture["site_marker"].exists()
    assert not fixture["worker_marker"].exists()
    assert not hostile_marker.exists()
    if case == "valid-pyc":
        assert cache_path.is_file()
        assert not fixture["preflight_import_marker"].exists()
        cache_path.unlink()
        external_cache = tmp_path / "untrusted-module.pyc"
        external_cache.write_bytes(b"not imported; symlink target must be rejected")
        cache_link = fixture["runtime_root"] / "salience" / "untrusted-module.py"
        cache_link.symlink_to(external_cache)
        symlink_result = _run_local_supported_launcher(fixture)
        assert symlink_result.returncode == 78, symlink_result.stderr
        assert "symlink to .pyc" in symlink_result.stderr
        assert not fixture["preflight_import_marker"].exists()
        assert not fixture["worker_marker"].exists()


@pytest.mark.parametrize(
    "worker_kind",
    ("deployable", "legacy-fixture"),
    ids=("deployable-entrypoint", "legacy-fixture-entrypoint"),
)
def test_supported_launcher_read_only_root_creates_no_pyc(tmp_path, monkeypatch, worker_kind):
    fixture = _write_local_launcher_process_fixture(tmp_path, monkeypatch, allow_worker=True)

    def pyc_paths():
        return sorted(path.relative_to(fixture["runtime_root"]).as_posix()
                      for path in fixture["runtime_root"].rglob("*.pyc"))

    before_files = {
        path.relative_to(fixture["runtime_root"]).as_posix(): _sha256(path.read_bytes())
        for path in fixture["runtime_root"].rglob("*")
        if path.is_file()
    }
    writable_control = _run_local_supported_launcher(fixture, worker_kind=worker_kind)
    assert writable_control.returncode == 0, writable_control.stderr
    assert fixture["preflight_import_marker"].read_text(encoding="utf-8") == "1,1,1"
    assert fixture["worker_marker"].read_text(encoding="utf-8") == "1,1,1"
    assert pyc_paths() == []
    fixture["worker_marker"].unlink()

    for path in sorted(fixture["runtime_root"].rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    fixture["runtime_root"].chmod(0o555)
    try:
        read_only_control = _run_local_supported_launcher(fixture, worker_kind=worker_kind)
        assert read_only_control.returncode == 0, read_only_control.stderr
        assert fixture["worker_marker"].read_text(encoding="utf-8") == "1,1,1"
        assert pyc_paths() == []
        after_files = {
            path.relative_to(fixture["runtime_root"]).as_posix(): _sha256(path.read_bytes())
            for path in fixture["runtime_root"].rglob("*")
            if path.is_file()
        }
        assert after_files == before_files
    finally:
        fixture["runtime_root"].chmod(0o755)
        for path in sorted(fixture["runtime_root"].rglob("*"), reverse=True):
            path.chmod(0o755 if path.is_dir() else 0o644)


def _write_task8_candidate_metadata_fixture(candidate_root):
    control_bundle = candidate_root / "control-bundle"
    control_bundle.mkdir(parents=True)
    launcher_bytes = b"synthetic no-effects trusted launcher source\n"
    launcher_path = control_bundle / "launch-v4-publication-worker.py"
    launcher_path.write_bytes(launcher_bytes)
    app_commit = "a" * 40
    image_id = "sha256:" + "d" * 64
    image_ref = f"salience-p1-floor-{app_commit[:7]}-worker:latest"
    lock = {
        "schema_version": "salience.publication-worker-launch-lock.v1",
        "interpreter_path": "/usr/local/bin/python",
        "interpreter_sha256": "e" * 64,
        "launcher_sha256": _sha256(launcher_bytes),
        "runtime_root": "/usr/local/lib/python3.13/site-packages",
        "workers": {
            "deployable": {"module": "salience.workflows.worker", "argv": []},
            "legacy-fixture": {"module": "salience.cycles.legacy_runtime", "argv": []},
        },
        "pin": {
            "schema_version": "salience.publication-worker-pin.v1",
            "application_commit": app_commit,
            "eligible_anchor": ANCHOR,
            "artifact_path": "/opt/salience-artifacts/salience.whl",
            "artifact_sha256": "b" * 64,
            "distribution_record_path": (
                "/usr/local/lib/python3.13/site-packages/salience-0.1.0.dist-info/RECORD"
            ),
            "distribution_record_sha256": "c" * 64,
            "source_manifest_sha256": "1" * 64,
            "source_files": [
                {
                    "path": "scripts/launch-v4-publication-worker.py",
                    "git_blob": "2" * 40,
                    "sha256": _sha256(launcher_bytes),
                }
            ],
            "runtime_imports": [],
        },
    }
    module_paths = {
        "salience.cycles.publication_recovery_compatibility": "salience/cycles/publication_recovery_compatibility.py",
        "salience.cycles.legacy_runtime": "salience/cycles/legacy_runtime.py",
        "salience.cycles.legacy_publication_runtime": "salience/cycles/legacy_publication_runtime.py",
        "salience.cycles.publication_recovery": "salience/cycles/publication_recovery.py",
        "salience.cycles.legacy_publication": "salience/cycles/legacy_publication.py",
        "salience.workflows.worker": "salience/workflows/worker.py",
        "salience.workflows.publication": "salience/workflows/publication.py",
        "salience.workflows.persistence": "salience/workflows/persistence.py",
        "salience.publication.repository": "salience/publication/repository.py",
    }
    lock["pin"]["runtime_imports"] = [
        {
            "module": module,
            "observed_path": lock["runtime_root"] + "/" + path,
            "wheel_path": path,
            "sha256": "4" * 64,
        }
        for module, path in module_paths.items()
    ]
    lock["pin"]["source_manifest_sha256"] = _canonical_json_hash(
        lock["pin"]["source_files"]
    )
    lock_bytes = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    (control_bundle / "publication-worker-launch-lock.json").write_bytes(lock_bytes)
    lock_digest = _sha256(lock_bytes)
    (control_bundle / "publication-worker-launch-lock.sha256").write_text(
        lock_digest + "\n", encoding="ascii"
    )
    (candidate_root / "worker-image-id.txt").write_text(image_id + "\n", encoding="ascii")
    attestation = {
        "application_commit": app_commit,
        "image_ref": image_ref,
        "image_id": image_id,
        "wheel_sha256": lock["pin"]["artifact_sha256"],
        "record_sha256": lock["pin"]["distribution_record_sha256"],
        "record_ok": True,
        "pyc_files": [],
        "source_tree_loaded": False,
        "runtime_root": lock["runtime_root"],
    }
    (candidate_root / "image-attestation.json").write_text(
        json.dumps(attestation, sort_keys=True) + "\n", encoding="utf-8"
    )
    workspace_id = "00000000-0000-0000-0000-000000000001"
    operation_ids = {
        "expired_permit_receipt": "00000000-0000-0000-0000-000000000002",
        "published_during_cancel": "00000000-0000-0000-0000-000000000003",
    }
    hold_ids = {
        "expired_permit_receipt": "00000000-0000-0000-0000-000000000004",
        "published_during_cancel": "00000000-0000-0000-0000-000000000005",
    }
    history_fixture = {
        reason: {
            "workflow_id": "salience-v4-legacy-operation:" + operation_ids[reason],
            "task_queue": "task8-fixture-" + reason,
            "last_event_id": 10,
            "history_sha256": "9" * 64,
        }
        for reason in operation_ids
    }
    legacy_operation_ids = {
        "expired_permit_receipt": operation_ids["expired_permit_receipt"],
        "published_during_cancel": "00000000-0000-0000-0000-000000000007",
    }
    legacy_hold_ids = {
        "expired_permit_receipt": hold_ids["expired_permit_receipt"],
        "published_during_cancel": "00000000-0000-0000-0000-000000000008",
    }
    primary_target = {
        "database_name": "recovery_floor_active_0123456789ab",
        "database_host": "127.0.0.1",
        "database_port": 5432,
        "temporal_target": "127.0.0.1:7233",
        "adapter_recorder": {
            "base_url": "http://127.0.0.1:8081",
            "run_id": "00000000-0000-0000-0000-000000000006",
        },
        "fixture_lifetime": "task8-persistent",
    }
    legacy_target = {
        **primary_target,
        "database_name": "recovery_floor_legacy_0123456789ab",
    }
    hold_rows = {
        reason: {
            "id": hold_ids[reason],
            "operation_id": operation_ids[reason],
            "workspace_id": workspace_id,
            "reason": reason,
            "liability_snapshot_version": "salience.publication-liability.v1",
        }
        for reason in operation_ids
    }
    liabilities = {
        reason: {"operation_id": operation_ids[reason], "fixture": "synthetic-no-effects"}
        for reason in operation_ids
    }
    legacy_hold_rows = {
        reason: {
            "id": legacy_hold_ids[reason],
            "operation_id": legacy_operation_ids[reason],
            "workspace_id": workspace_id,
            "reason": reason,
            "liability_snapshot_version": (
                None if reason == "published_during_cancel" else "salience.publication-liability.v1"
            ),
        }
        for reason in operation_ids
    }
    legacy_liabilities = {
        reason: {
            "operation_id": legacy_operation_ids[reason],
            "fixture": "synthetic-no-effects-legacy-null",
        }
        for reason in operation_ids
    }
    primary_state = {
        "head": "0043_complete_publication_recovery_liabilities",
        "workspace_id": workspace_id,
        "holds": hold_rows,
        "liabilities": liabilities,
    }
    legacy_state = {
        "head": "0043_complete_publication_recovery_liabilities",
        "workspace_id": workspace_id,
        "holds": legacy_hold_rows,
        "liabilities": legacy_liabilities,
        "legacy_null_v1": {
            "operation_id": legacy_operation_ids["published_during_cancel"],
            "fields": [None, None, None],
        },
    }
    hashes = {
        "liability_snapshot_hashes": {
            reason: _canonical_json_hash(liabilities[reason]) for reason in operation_ids
        },
        "hold_row_hashes": {
            reason: _canonical_json_hash(hold_rows[reason]) for reason in operation_ids
        },
    }
    legacy_hashes = {
        "liability_snapshot_hashes": {
            reason: _canonical_json_hash(legacy_liabilities[reason]) for reason in operation_ids
        },
        "hold_row_hashes": {
            reason: _canonical_json_hash(legacy_hold_rows[reason]) for reason in operation_ids
        },
    }
    fixture = {
        "schema_version": "salience.publication-floor-rollback-launch-fixture.v1",
        "application_commit": app_commit,
        "image_ref": image_ref,
        "image_id": image_id,
        "launch_lock_sha256": lock_digest,
        "disposable_targets": primary_target,
        "workspace_id": workspace_id,
        "operation_ids": operation_ids,
        "hold_ids": hold_ids,
        "history_fixture": history_fixture,
        **hashes,
    }
    legacy_fixture = {
        "schema_version": "salience.publication-floor-legacy-null-v1-fixture.v1",
        "application_commit": app_commit,
        "image_ref": image_ref,
        "image_id": image_id,
        "launch_lock_sha256": lock_digest,
        "disposable_targets": legacy_target,
        "workspace_id": workspace_id,
        "operation_ids": legacy_operation_ids,
        "hold_ids": legacy_hold_ids,
        "execution_mode": {
            "mode": "preflight_only",
            "denial_stage": "before_worker_registration",
            "worker_started": False,
            "outbox_dispatched": False,
        },
        "legacy_null_operation_id": legacy_operation_ids["published_during_cancel"],
        **legacy_hashes,
    }
    manifest = {
        "files": {"salience/workflows/worker.py": "sha256:" + "5" * 64},
        "pyc_files": [],
    }
    manifest_digest = _canonical_json_hash(manifest)
    attestation["runtime_manifest_sha256"] = manifest_digest
    (candidate_root / "image-attestation.json").write_text(
        json.dumps(attestation, sort_keys=True) + "\n", encoding="utf-8"
    )
    (candidate_root / "worker-image-id.txt").write_text(image_id + "\n", encoding="ascii")

    harness = _load_worker_harness()
    producer_inputs = {
        "rollback_launch_fixture": fixture,
        "legacy_null_v1_fixture": legacy_fixture,
        "persistent_fixture": True,
        "read_database_state": lambda: primary_state,
        "read_legacy_database_state": lambda: legacy_state,
        "read_histories": lambda _target, histories: histories,
        "read_recorder_events": lambda _url, _run_id: [],
        "image_inspector": lambda _reference: image_id,
        "runtime_manifest_reader": lambda _image, _runtime: manifest,
    }
    asyncio.run(harness.write_qualification_progress(candidate_root, **producer_inputs))
    return {
        "candidate_root": candidate_root,
        "environment": {
            "TEST_DATABASE_URL": "postgresql://127.0.0.1:5432/recovery_floor_active_0123456789ab",
            "TEST_LEGACY_NULL_DATABASE_URL": "postgresql://127.0.0.1:5432/recovery_floor_legacy_0123456789ab",
            "TEST_TEMPORAL_TARGET": "127.0.0.1:7233",
        },
        "fixture": fixture,
        "legacy_fixture": legacy_fixture,
        "producer_inputs": producer_inputs,
        "lock": lock,
        "attestation": attestation,
    }


def test_task8_candidate_loader_validates_attestation_control_and_both_reason_metadata(tmp_path):
    synthetic = _write_task8_candidate_metadata_fixture(tmp_path / "candidate")
    candidate = _task8_rollback_image_or_skip(
        candidate_root=synthetic["candidate_root"],
        environment=synthetic["environment"],
    )
    assert candidate["image_id"] == synthetic["attestation"]["image_id"]
    assert candidate["lock"]["pin"]["application_commit"] == synthetic["fixture"]["application_commit"]
    assert candidate["fixture"]["operation_ids"] == synthetic["fixture"]["operation_ids"]
    assert candidate["database_url"] == synthetic["environment"]["TEST_DATABASE_URL"]
    assert candidate["temporal_target"] == synthetic["environment"]["TEST_TEMPORAL_TARGET"]


def test_candidate_control_bundle_authenticates_full_0043_inventory(
    task8_candidate_state, task8_candidate_database, tmp_path
):
    artifact_root = task8_candidate_state["candidate_root"]
    control_bundle = task8_candidate_state["control_bundle"]
    lock_path = ROOT / "deployment/v4/publication-worker-launch-lock.json"
    digest_path = artifact_root / "publication-worker-launch-lock.sha256"
    wheel_path = artifact_root / "salience.whl"
    runtime_root = task8_candidate_state["runtime_path"]
    assert control_bundle.is_dir()
    assert runtime_root.is_dir()
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    application_commit = lock["pin"]["application_commit"]
    assert lock_bytes == (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert set(path.name for path in control_bundle.iterdir()) == {
        "launch-v4-publication-worker.py",
        "publication-worker-launch-lock.json",
        "publication-worker-launch-lock.sha256",
    }
    for name, expected in (
        ("launch-v4-publication-worker.py", ROOT / "scripts/launch-v4-publication-worker.py"),
        ("publication-worker-launch-lock.json", lock_path),
        ("publication-worker-launch-lock.sha256", digest_path),
    ):
        actual = control_bundle / name
        assert actual.is_file() and not actual.is_symlink()
        assert actual.read_bytes() == expected.read_bytes()
    digest = _sha256(lock_bytes)
    assert digest_path.read_text(encoding="ascii") == digest + "\n"

    spec = importlib.util.spec_from_file_location(
        "task8_publication_worker_lock_builder",
        ROOT / "scripts/build_publication_worker_launch_lock.py",
    )
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    tree = builder._tracked_tree(application_commit, ROOT)
    expected_paths = set(tree)
    source_pins = lock["pin"]["source_files"]
    assert {entry["path"] for entry in source_pins} == expected_paths
    assert [entry["path"] for entry in source_pins] == sorted(expected_paths)
    for entry in source_pins:
        content = tree[entry["path"]]
        assert entry["git_blob"] == builder._git_blob_sha1(content)
        assert entry["sha256"] == _sha256(content)
    assert lock["pin"]["source_manifest_sha256"] == _canonical_json_hash(source_pins)
    revisions = {
        int(Path(path).name[:4])
        for path in expected_paths
        if path.startswith("migrations/versions/")
        and re.fullmatch(r"\d{4}_.+\.py", Path(path).name)
    }
    assert revisions == set(range(1, 44))
    builder._validate_installed_source_tree(tree, runtime_root)
    expected_package = {
        path.removeprefix("src/"): content
        for path, content in tree.items()
        if path.startswith("src/salience/")
    }
    record_relative, record_bytes = builder._validate_wheel_and_installed_record(
        wheel_path, runtime_root, expected_package
    )
    assert str(Path(lock["runtime_root"]) / record_relative) == lock["pin"][
        "distribution_record_path"
    ]
    assert _sha256(wheel_path.read_bytes()) == lock["pin"]["artifact_sha256"]
    assert _sha256(record_bytes) == lock["pin"]["distribution_record_sha256"]
    image_id = task8_candidate_state["image_id"]

    def invoke_case(
        name,
        *,
        candidate_lock=None,
        independent_digest=None,
        extra_mounts=(),
        extra_environment=None,
        workdir=None,
    ):
        result = _load_worker_harness().invoke_launcher(
            image=image_id,
            database_url=task8_candidate_database,
            control_bundle=control_bundle,
            candidate_lock=candidate_lock,
            independent_digest=independent_digest,
            extra_mounts=extra_mounts,
            extra_environment=extra_environment,
            workdir=workdir,
            argv=(
                "--mode",
                "rollback-restart",
                "--worker-kind",
                "legacy-fixture",
                "--workspace-id",
                "00000000-0000-0000-0000-000000000099",
                "--expired-hold-operation-id",
                "00000000-0000-0000-0000-000000000098",
                "--cancel-hold-operation-id",
                "00000000-0000-0000-0000-000000000097",
            ),
        )
        diagnostic = (result.stdout + result.stderr).casefold()
        assert result.returncode == 78, f"{name}: {diagnostic}"
        assert "execve" not in diagnostic and "worker import" not in diagnostic
        return diagnostic

    exact_diagnostic = invoke_case("exact-inventory")
    assert "both retained publication recovery holds failed verification" in exact_diagnostic

    def runtime_copy(label):
        root = tmp_path / label / "site-packages"
        for relative, content in tree.items():
            if relative.startswith("src/salience/"):
                target = root / relative.removeprefix("src/")
            elif relative.startswith("migrations/") or relative == "alembic.ini":
                target = root / relative
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return root

    extra_runtime = runtime_copy("extra-file")
    (extra_runtime / "migrations/versions/0044_task8_valid_descendant.py").write_text(
        "revision = '0044_task8_valid_descendant'\n"
        "down_revision = '0043_complete_publication_recovery_liabilities'\n"
        "branch_labels = None\n"
        "depends_on = None\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="runtime inventory does not match"):
        builder._validate_installed_source_tree(tree, extra_runtime)
    extra_physical_diagnostic = invoke_case(
        "physical extra valid descendant",
        extra_mounts=(
            (
                extra_runtime / "migrations/versions",
                lock["runtime_root"] + "/migrations/versions",
            ),
        ),
    )
    assert "authenticated migration/config inventory does not match runtime files" in extra_physical_diagnostic

    extra_lock = json.loads(lock_bytes)
    extra_lock["pin"]["source_files"].append(
        {
            "path": "migrations/versions/0044_task8_valid_descendant.py",
            "git_blob": "0" * 40,
            "sha256": "0" * 64,
        }
    )
    extra_lock["pin"]["source_files"].sort(key=lambda entry: entry["path"])
    extra_lock["pin"]["source_manifest_sha256"] = _canonical_json_hash(
        extra_lock["pin"]["source_files"]
    )
    extra_lock_path = tmp_path / "extra-file.lock.json"
    extra_digest_path = tmp_path / "extra-file.lock.sha256"
    extra_raw = (json.dumps(extra_lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    extra_lock_path.write_bytes(extra_raw)
    extra_digest_path.write_text(_sha256(extra_raw) + "\n", encoding="ascii")
    extra_diagnostic = invoke_case(
        "extra-file", candidate_lock=extra_lock_path, independent_digest=extra_digest_path
    )
    assert "source pin mismatch" in extra_diagnostic or "launch pin is invalid" in extra_diagnostic

    missing_runtime = runtime_copy("missing-file")
    (missing_runtime / "migrations/versions/0043_complete_publication_recovery_liabilities.py").unlink()
    with pytest.raises(ValueError, match="runtime inventory does not match"):
        builder._validate_installed_source_tree(tree, missing_runtime)
    missing_physical_diagnostic = invoke_case(
        "physical missing revision",
        extra_mounts=(
            (
                missing_runtime / "migrations/versions",
                lock["runtime_root"] + "/migrations/versions",
            ),
        ),
    )
    assert (
        "publication worker launch denied: publication worker launch pin is invalid"
        in missing_physical_diagnostic
    )

    missing_lock = json.loads(lock_bytes)
    missing_lock["pin"]["source_files"] = [
        entry for entry in missing_lock["pin"]["source_files"]
        if entry["path"] != "migrations/versions/0043_complete_publication_recovery_liabilities.py"
    ]
    missing_lock["pin"]["source_manifest_sha256"] = _canonical_json_hash(
        missing_lock["pin"]["source_files"]
    )
    missing_lock_path = tmp_path / "missing-file.lock.json"
    missing_digest_path = tmp_path / "missing-file.lock.sha256"
    missing_raw = (json.dumps(missing_lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    missing_lock_path.write_bytes(missing_raw)
    missing_digest_path.write_text(_sha256(missing_raw) + "\n", encoding="ascii")
    missing_diagnostic = invoke_case(
        "missing-file", candidate_lock=missing_lock_path, independent_digest=missing_digest_path
    )
    assert "publication worker source manifest is incomplete" in missing_diagnostic

    changed_runtime = runtime_copy("changed-file")
    changed_path = changed_runtime / "migrations/versions/0043_complete_publication_recovery_liabilities.py"
    changed_path.write_bytes(changed_path.read_bytes() + b"\n# task8 mutation\n")
    with pytest.raises(ValueError, match="differs from application_commit"):
        builder._validate_installed_source_tree(tree, changed_runtime)
    changed_physical_diagnostic = invoke_case(
        "physical changed migration bytes",
        extra_mounts=(
            (
                changed_runtime / "migrations/versions",
                lock["runtime_root"] + "/migrations/versions",
            ),
        ),
    )
    assert "publication worker source pin mismatch" in changed_physical_diagnostic

    changed_down_revision_runtime = runtime_copy("changed-down-revision")
    changed_down_revision_path = (
        changed_down_revision_runtime
        / "migrations/versions/0043_complete_publication_recovery_liabilities.py"
    )
    changed_down_revision = changed_down_revision_path.read_text(encoding="utf-8").replace(
        'down_revision = "0042_publication_recovery_holds"',
        'down_revision = "0041_native_publication_sources"',
        1,
    )
    assert changed_down_revision != changed_down_revision_path.read_text(encoding="utf-8")
    changed_down_revision_path.write_text(changed_down_revision, encoding="utf-8")
    changed_down_revision_diagnostic = invoke_case(
        "physical changed down_revision bytes",
        extra_mounts=(
            (
                changed_down_revision_runtime / "migrations/versions",
                lock["runtime_root"] + "/migrations/versions",
            ),
        ),
    )
    assert "publication worker source pin mismatch" in changed_down_revision_diagnostic

    changed_lock = json.loads(lock_bytes)
    changed_pin = next(
        entry for entry in changed_lock["pin"]["source_files"]
        if entry["path"] == "migrations/versions/0043_complete_publication_recovery_liabilities.py"
    )
    changed_pin["sha256"] = "f" * 64
    changed_lock["pin"]["source_manifest_sha256"] = _canonical_json_hash(
        changed_lock["pin"]["source_files"]
    )
    changed_lock_path = tmp_path / "changed-file.lock.json"
    changed_digest_path = tmp_path / "changed-file.lock.sha256"
    changed_raw = (json.dumps(changed_lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    changed_lock_path.write_bytes(changed_raw)
    changed_digest_path.write_text(_sha256(changed_raw) + "\n", encoding="ascii")
    assert "source pin mismatch" in invoke_case(
        "changed-file", candidate_lock=changed_lock_path, independent_digest=changed_digest_path
    )

    symlink_runtime = runtime_copy("symlink-path")
    symlinked_revision = symlink_runtime / "migrations/versions/0043_complete_publication_recovery_liabilities.py"
    symlinked_revision.unlink()
    symlinked_revision.symlink_to("/tmp/task8-outside-authenticated-root.py")
    with pytest.raises(ValueError, match="contains a symlink"):
        builder._validate_installed_source_tree(tree, symlink_runtime)
    overlay_versions = tmp_path / "symlink-overlay"
    shutil.copytree(
        symlink_runtime / "migrations/versions", overlay_versions, symlinks=True
    )
    escaped_source = tmp_path / "outside-authenticated-root.py"
    escaped_source.write_text("# outside the authenticated runtime root\n", encoding="utf-8")
    symlink_diagnostic = invoke_case(
        "symlink-path",
        extra_mounts=(
            (overlay_versions, lock["runtime_root"] + "/migrations/versions"),
            (escaped_source, "/tmp/task8-outside-authenticated-root.py"),
        ),
    )
    assert "pinned file escapes authenticated runtime root" in symlink_diagnostic

    bad_digest_path = tmp_path / "independent-digest-mismatch.sha256"
    bad_digest_path.write_text("0" * 64 + "\n", encoding="ascii")
    digest_diagnostic = invoke_case(
        "independent-digest-mismatch", independent_digest=bad_digest_path
    )
    assert "independent trust root" in digest_diagnostic


def test_exact_candidate_image_denies_real_empty_0042_before_registration(
    task8_candidate_state, task8_empty_0042_database
):
    harness = _load_worker_harness()
    fixture_workspace = "00000000-0000-0000-0000-000000000099"
    result = harness.invoke_launcher(
        image=task8_candidate_state["image_id"],
        database_url=task8_empty_0042_database,
        control_bundle=task8_candidate_state["control_bundle"],
        argv=(
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "legacy-fixture",
            "--workspace-id",
            fixture_workspace,
            "--expired-hold-operation-id",
            "00000000-0000-0000-0000-000000000098",
            "--cancel-hold-operation-id",
            "00000000-0000-0000-0000-000000000097",
        ),
    )
    diagnostic = (result.stdout + result.stderr).casefold()
    assert result.returncode == 78, diagnostic
    assert "database schema floor 0043 is not satisfied" in diagnostic
    assert "execve" not in diagnostic and "worker import" not in diagnostic


@pytest.mark.parametrize("hostile_surface", ["pythonpath", "pythonhome", "cwd-shadow"])
def test_exact_candidate_image_ignores_hostile_python_environment_and_cwd(
    task8_candidate_state, task8_candidate_database, tmp_path, hostile_surface
):
    harness = _load_worker_harness()
    hostile_root = tmp_path / hostile_surface
    hostile_root.mkdir()
    marker = "TASK8_HOSTILE_IMPORT_EXECUTED_" + hostile_surface.upper().replace("-", "_")
    (hostile_root / "sitecustomize.py").write_text(
        f"print({marker!r})\n", encoding="utf-8"
    )
    shadow_package = hostile_root / "salience" / "cycles"
    shadow_package.mkdir(parents=True)
    (hostile_root / "salience" / "__init__.py").write_text(
        f"print({marker!r})\n", encoding="utf-8"
    )
    (shadow_package / "__init__.py").write_text(
        f"print({marker!r})\n", encoding="utf-8"
    )
    (shadow_package / "publication_recovery_compatibility.py").write_text(
        f"print({marker!r})\n", encoding="utf-8"
    )

    extra_environment = {}
    workdir = None
    if hostile_surface == "pythonpath":
        extra_environment["PYTHONPATH"] = "/tmp/task8-hostile-pythonpath"
        container_hostile_root = "/tmp/task8-hostile-pythonpath"
    elif hostile_surface == "pythonhome":
        extra_environment["PYTHONHOME"] = "/tmp/task8-hostile-pythonhome"
        container_hostile_root = "/tmp/task8-hostile-pythonhome"
    else:
        container_hostile_root = "/tmp/task8-hostile-cwd"
        workdir = container_hostile_root

    result = harness.invoke_launcher(
        image=task8_candidate_state["image_id"],
        database_url=task8_candidate_database,
        control_bundle=task8_candidate_state["control_bundle"],
        extra_mounts=((hostile_root, container_hostile_root),),
        extra_environment=extra_environment,
        workdir=workdir,
        argv=(
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "legacy-fixture",
            "--workspace-id",
            "00000000-0000-0000-0000-000000000099",
            "--expired-hold-operation-id",
            "00000000-0000-0000-0000-000000000098",
            "--cancel-hold-operation-id",
            "00000000-0000-0000-0000-000000000097",
        ),
    )
    diagnostic = (result.stdout + result.stderr).casefold()
    assert result.returncode == 78, diagnostic
    assert "both retained publication recovery holds failed verification" in diagnostic
    assert marker.casefold() not in diagnostic


def test_exact_candidate_image_rejects_valid_malicious_pyc_with_positive_control(
    task8_candidate_state, task8_candidate_database, tmp_path
):
    import importlib._bootstrap_external
    import importlib.util
    import shutil
    import struct

    candidate = task8_candidate_state
    harness = _load_worker_harness()
    source_relative = "salience/cycles/publication_recovery_compatibility.py"
    source_in_image = Path(candidate["runtime_root"]) / source_relative
    metadata_probe = (
        "import importlib.util,json,pathlib,sys;"
        f"p=pathlib.Path({str(source_in_image)!r});s=p.stat();"
        "print(json.dumps({'mtime':int(s.st_mtime),'size':s.st_size,"
        "'magic':importlib.util.MAGIC_NUMBER.hex(),"
        "'cache':pathlib.Path(importlib.util.cache_from_source(str(p))).name}))"
    )
    metadata = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--network=none",
            candidate["image_id"],
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            "-c",
            metadata_probe,
        ],
        capture_output=True,
        text=True,
        env=harness._docker_environment(),
        timeout=30,
        check=False,
    )
    assert metadata.returncode == 0, metadata.stderr
    target = json.loads(metadata.stdout.strip().splitlines()[-1])
    assert target["magic"] == importlib.util.MAGIC_NUMBER.hex()

    overlay = tmp_path / "malicious-runtime"
    cycles_source = candidate["runtime_path"] / "salience/cycles"
    cycles_overlay = overlay / "salience/cycles"
    shutil.copytree(
        cycles_source,
        cycles_overlay,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    cache_dir = cycles_overlay / "__pycache__"
    cache_dir.mkdir()
    marker = "TASK8_VALID_MALICIOUS_PYC_EXECUTED"
    code = compile(f"print({marker!r})\n", str(source_in_image), "exec")
    pyc = importlib._bootstrap_external._code_to_timestamp_pyc(
        code, target["mtime"], target["size"]
    )
    assert pyc[:4].hex() == target["magic"]
    assert struct.unpack("<I", pyc[4:8])[0] == 0
    (cache_dir / target["cache"]).write_bytes(pyc)

    mount = (
        cycles_overlay,
        candidate["runtime_root"] + "/salience/cycles",
    )
    positive_probe = (
        "import importlib.util,sys;"
        f"p={str(source_in_image)!r};"
        "spec=importlib.util.spec_from_file_location("
        "'salience.cycles.publication_recovery_compatibility',p);"
        "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)"
    )
    positive = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--network=none",
            "--mount",
            harness._mount(*mount),
            candidate["image_id"],
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            "-c",
            positive_probe,
        ],
        capture_output=True,
        text=True,
        env=harness._docker_environment(),
        timeout=30,
        check=False,
    )
    assert positive.returncode == 0, positive.stderr
    assert marker in positive.stdout

    denial = harness.invoke_launcher(
        image=candidate["image_id"],
        database_url=task8_candidate_database,
        control_bundle=candidate["control_bundle"],
        extra_mounts=(mount,),
        argv=(
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "legacy-fixture",
            "--workspace-id",
            "00000000-0000-0000-0000-000000000099",
            "--expired-hold-operation-id",
            "00000000-0000-0000-0000-000000000098",
            "--cancel-hold-operation-id",
            "00000000-0000-0000-0000-000000000097",
        ),
    )
    diagnostic = (denial.stdout + denial.stderr).casefold()
    assert denial.returncode == 78, diagnostic
    assert "authenticated runtime root contains a .pyc entry" in diagnostic
    assert marker.casefold() not in diagnostic
    assert "execve" not in diagnostic and "worker import" not in diagnostic


def test_task8_loader_never_falls_back_to_legacy_candidate_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    tracked_lock = tmp_path / "deployment/v4/publication-worker-launch-lock.json"
    tracked_lock.parent.mkdir(parents=True)
    tracked_lock.write_text(
        json.dumps({"pin": {"application_commit": "a" * 40}}), encoding="utf-8"
    )
    legacy_root = (
        tmp_path / "artifacts/v4-next-p1/agents/task-10-floor-qualification"
        / "candidate-5cb5791"
    )
    legacy_root.mkdir(parents=True)
    with pytest.raises(AssertionError, match="legacy Task 10 candidate provenance is rejected"):
        _task8_rollback_image_or_skip(
            candidate_root=legacy_root,
            environment={},
        )

    with pytest.raises(pytest.skip.Exception, match="Task 8 candidate fixture metadata is absent"):
        _task8_rollback_image_or_skip(environment={})


def test_task8_qualification_progress_producer_round_trips_through_loader(tmp_path):
    synthetic = _write_task8_candidate_metadata_fixture(tmp_path / "candidate")
    harness = _load_worker_harness()
    candidate = _task8_rollback_image_or_skip(
        candidate_root=synthetic["candidate_root"],
        environment=synthetic["environment"],
    )
    assert candidate["progress"]["runtime_manifest_sha256"]
    assert set(candidate["progress"]) == harness.QUALIFICATION_PROGRESS_FIELDS
    assert not tuple(synthetic["candidate_root"].glob(".worker-qualification-progress-*.tmp"))
    assert candidate["legacy_null_fixture"]["legacy_null_operation_id"] != (
        candidate["fixture"]["operation_ids"]["published_during_cancel"]
    )


@pytest.mark.parametrize(
    "failure",
    ("malformed-state", "stale-state", "ephemeral-target"),
    ids=("malformed-readback", "stale-readback", "ephemeral-target"),
)
def test_task8_qualification_progress_producer_rejects_unqualified_state(tmp_path, failure):
    synthetic = _write_task8_candidate_metadata_fixture(tmp_path / failure)
    harness = _load_worker_harness()
    inputs = synthetic["producer_inputs"]
    if failure == "malformed-state":
        inputs["read_database_state"] = lambda: {"head": "not-0043"}
    elif failure == "stale-state":
        actual = inputs["read_database_state"]()
        actual["holds"]["expired_permit_receipt"]["id"] = "00000000-0000-0000-0000-000000000099"
        inputs["read_database_state"] = lambda: actual
    else:
        inputs["rollback_launch_fixture"]["disposable_targets"]["fixture_lifetime"] = (
            "function-scoped"
        )
    progress_path = synthetic["candidate_root"] / "worker-qualification-progress.json"
    before = progress_path.read_bytes()
    with pytest.raises((AssertionError, ValueError), match="persist|read back|0043|hold"):
        asyncio.run(
            harness.write_qualification_progress(synthetic["candidate_root"], **inputs)
        )
    assert progress_path.read_bytes() == before


def test_task8_candidate_loader_rejects_missing_fixture_and_mismatched_attestation(tmp_path):
    incomplete = _write_task8_candidate_metadata_fixture(tmp_path / "incomplete")
    progress_path = incomplete["candidate_root"] / "worker-qualification-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress.pop("rollback_launch_fixture")
    progress_path.write_text(json.dumps(progress) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="progress schema fields must match exactly"):
        _task8_rollback_image_or_skip(
            candidate_root=incomplete["candidate_root"],
            environment=incomplete["environment"],
        )

    mismatched = _write_task8_candidate_metadata_fixture(tmp_path / "mismatched")
    (mismatched["candidate_root"] / "worker-image-id.txt").write_text(
        "sha256:" + "a" * 64 + "\n", encoding="ascii"
    )
    with pytest.raises(AssertionError):
        _task8_rollback_image_or_skip(
            candidate_root=mismatched["candidate_root"],
            environment=mismatched["environment"],
        )


def test_task8_producer_requires_primary_histories_and_legacy_preflight_mode(tmp_path):
    for fixture_name, mutation in (
        ("rollback_launch_fixture", "omit-primary-history"),
        ("legacy_null_v1_fixture", "claim-legacy-history"),
    ):
        synthetic = _write_task8_candidate_metadata_fixture(
            tmp_path / mutation
        )
        fixture = synthetic["producer_inputs"][fixture_name]
        if mutation == "omit-primary-history":
            fixture.pop("history_fixture")
        else:
            fixture["history_fixture"] = {
                "published_during_cancel": {
                    "workflow_id": "salience-v4-legacy-operation:"
                    + fixture["legacy_null_operation_id"],
                    "task_queue": "forbidden-legacy-replay",
                    "last_event_id": 1,
                    "history_sha256": "9" * 64,
                }
            }
        progress_path = synthetic["candidate_root"] / "worker-qualification-progress.json"
        before = progress_path.read_bytes()
        with pytest.raises(AssertionError):
            asyncio.run(
                _load_worker_harness().write_qualification_progress(
                    synthetic["candidate_root"],
                    **synthetic["producer_inputs"],
                )
            )
        assert progress_path.read_bytes() == before


def test_task8_loader_rejects_legacy_fixture_that_claims_temporal_history(tmp_path):
    synthetic = _write_task8_candidate_metadata_fixture(tmp_path / "legacy-history")
    progress_path = synthetic["candidate_root"] / "worker-qualification-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    legacy_fixture = progress["legacy_null_v1_fixture"]
    legacy_fixture["history_fixture"] = {
        "published_during_cancel": {
            "workflow_id": "salience-v4-legacy-operation:"
            + legacy_fixture["legacy_null_operation_id"],
            "task_queue": "forbidden-legacy-replay",
            "last_event_id": 1,
            "history_sha256": "9" * 64,
        }
    }
    progress_path.write_text(json.dumps(progress) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        _task8_rollback_image_or_skip(
            candidate_root=synthetic["candidate_root"],
            environment=synthetic["environment"],
        )


def test_task8_loader_rejects_primary_fixture_that_omits_temporal_histories(tmp_path):
    synthetic = _write_task8_candidate_metadata_fixture(tmp_path / "primary-history")
    progress_path = synthetic["candidate_root"] / "worker-qualification-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress["rollback_launch_fixture"].pop("history_fixture")
    progress_path.write_text(json.dumps(progress) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        _task8_rollback_image_or_skip(
            candidate_root=synthetic["candidate_root"],
            environment=synthetic["environment"],
        )


def test_task8_recorder_container_exclusion_requires_exact_identity_and_command():
    harness = _load_worker_harness()
    image_id = "sha256:" + "d" * 64
    recorder = {
        "Image": image_id,
        "Name": "/task8-adapter-recorder-5988d97-5697a3ff",
        "Config": {
            "Entrypoint": ["/usr/local/bin/python"],
            "Cmd": [
                "-I",
                "-B",
                "-S",
                "/run/v4-publication-adapter-operation-receiver.py",
                "--host",
                "0.0.0.0",
                "--port",
                "8081",
            ],
            "Labels": {
                "com.docker.compose.project": "salience-p1-publication-recovery-0043",
                "com.docker.compose.service": "worker",
            },
        },
    }
    assert harness.is_task8_adapter_recorder_container(
        recorder, expected_image_id=image_id
    )

    lookalikes = []
    wrong_name = json.loads(json.dumps(recorder))
    wrong_name["Name"] = "/renamed-adapter-recorder-5988d97-5697a3ff"
    lookalikes.append(wrong_name)
    wrong_project = json.loads(json.dumps(recorder))
    wrong_project["Config"]["Labels"]["com.docker.compose.project"] = "other-project"
    lookalikes.append(wrong_project)
    wrong_service = json.loads(json.dumps(recorder))
    wrong_service["Config"]["Labels"]["com.docker.compose.service"] = "worker-recorder"
    lookalikes.append(wrong_service)
    partial_command = json.loads(json.dumps(recorder))
    partial_command["Config"]["Cmd"] = recorder["Config"]["Cmd"][:-2]
    lookalikes.append(partial_command)
    candidate_worker = json.loads(json.dumps(recorder))
    candidate_worker["Config"]["Cmd"] = [
        "-I",
        "-B",
        "-S",
        "-c",
        "runpy.run_path('/run/salience-control/launch-v4-publication-worker.py')",
    ]
    lookalikes.append(candidate_worker)
    other_image = json.loads(json.dumps(recorder))
    other_image["Image"] = "sha256:" + "e" * 64
    lookalikes.append(other_image)

    assert all(
        not harness.is_task8_adapter_recorder_container(
            lookalike, expected_image_id=image_id
        )
        for lookalike in lookalikes
    )


def test_task8_temporal_history_hash_is_deterministic_and_sensitive_to_event_changes():
    from temporalio.api.common.v1 import Memo
    from temporalio.api.history.v1 import (
        HistoryEvent,
        WorkflowExecutionStartedEventAttributes,
    )

    def event_with_memo_fields(keys, *, event_id=1):
        memo = Memo()
        for key, value in keys:
            memo.fields[key].data = value.encode()
        return HistoryEvent(
            event_id=event_id,
            workflow_execution_started_event_attributes=(
                WorkflowExecutionStartedEventAttributes(memo=memo)
            ),
        )

    forward = event_with_memo_fields((('zeta', 'last'), ('alpha', 'first')))
    reverse = event_with_memo_fields((('alpha', 'first'), ('zeta', 'last')))
    assert _temporal_history_sha256([forward]) == _temporal_history_sha256([reverse])

    changed_event = event_with_memo_fields(
        (('alpha', 'first'), ('zeta', 'last')), event_id=2
    )
    assert _temporal_history_sha256([forward]) != _temporal_history_sha256([changed_event])


def test_task8_external_history_exception_requires_exact_legacy_preflight_role():
    preflight_mode = {
        "mode": "preflight_only",
        "denial_stage": "before_worker_registration",
        "worker_started": False,
        "outbox_dispatched": False,
    }
    primary_histories = {
        "expired_permit_receipt": {"workflow_id": "required-primary-history"},
        "published_during_cancel": {"workflow_id": "required-primary-history"},
    }
    assert _task8_external_history_metadata(
        {
            "database_target": "primary",
            "fixture": {"history_fixture": primary_histories},
        }
    ) == primary_histories

    for history_fixture in (None, {}):
        fixture = {"execution_mode": preflight_mode}
        if history_fixture is not None:
            fixture["history_fixture"] = history_fixture
        assert _task8_external_history_metadata(
            {"database_target": "legacy-null-v1", "fixture": fixture}
        ) is None

    invalid_candidates = (
        {
            "database_target": "legacy-null-v1",
            "fixture": {"history_fixture": {}},
        },
        {
            "database_target": "legacy-null-v1",
            "fixture": {"execution_mode": {**preflight_mode, "worker_started": True}},
        },
        {
            "database_target": "legacy-null-v1",
            "fixture": {
                "execution_mode": preflight_mode,
                "history_fixture": {"published_during_cancel": {"workflow_id": "replay"}},
            },
        },
        {"database_target": "primary", "fixture": {"execution_mode": preflight_mode}},
        {"fixture": {"execution_mode": preflight_mode}},
        {"database_target": "primary", "fixture": {}},
    )
    for candidate in invalid_candidates:
        with pytest.raises(AssertionError):
            _task8_external_history_metadata(candidate)


@pytest.mark.parametrize("target", ("primary", "legacy-null-v1"))
def test_candidate_database_state_capture_uses_explicit_target_database(target, monkeypatch):
    import psycopg
    import test_v4_publication_recovery_holds as hold_helpers

    operation_ids = {
        "expired_permit_receipt": str(UUID(int=11 if target == "primary" else 21)),
        "published_during_cancel": str(UUID(int=12 if target == "primary" else 22)),
    }
    hold_ids = {
        reason: str(UUID(int=31 + index if target == "primary" else 41 + index))
        for index, reason in enumerate(operation_ids)
    }
    workspace_id = str(UUID(int=51 if target == "primary" else 61))
    database_url = (
        "postgresql://127.0.0.1/"
        + ("recovery_floor_active_0123456789ab" if target == "primary" else "recovery_floor_legacy_0123456789ab")
    )
    liabilities = {
        reason: {"job": {"id": str(UUID(int=71 + index))}}
        for index, reason in enumerate(operation_ids)
    }
    holds = {
        reason: {
            "id": hold_ids[reason],
            "operation_id": operation_id,
            "workspace_id": workspace_id,
            "reason": reason,
        }
        for reason, operation_id in operation_ids.items()
    }
    fixture = {
        "operation_ids": operation_ids,
        "hold_ids": hold_ids,
        "workspace_id": workspace_id,
        "liability_snapshot_hashes": {
            reason: _canonical_json_hash(snapshot)
            for reason, snapshot in liabilities.items()
        },
        "hold_row_hashes": {
            reason: _canonical_json_hash(snapshot)
            for reason, snapshot in holds.items()
        },
    }
    candidate = {
        "database_target": target,
        "database_url": database_url,
        "fixture": fixture,
    }
    connected_targets = []
    helper_targets = []

    class _VersionCursor:
        def fetchall(self):
            return [("0043_complete_publication_recovery_liabilities",)]

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, _statement):
            return _VersionCursor()

    def connect(selected_database_url):
        connected_targets.append(selected_database_url)
        return _Connection()

    def liability_snapshot(selected_database_url, operation_id):
        helper_targets.append(selected_database_url)
        return liabilities[next(
            reason for reason, value in operation_ids.items()
            if value == str(operation_id)
        )]

    def recovery_hold_snapshot(selected_database_url, operation_id):
        helper_targets.append(selected_database_url)
        return holds[next(
            reason for reason, value in operation_ids.items()
            if value == str(operation_id)
        )]

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(hold_helpers, "_liability_snapshot", liability_snapshot)
    monkeypatch.setattr(hold_helpers, "_recovery_hold_snapshot", recovery_hold_snapshot)

    state = _capture_candidate_database_state(candidate, database_url=database_url)

    assert connected_targets == [database_url]
    assert helper_targets == [database_url] * 4
    assert state == {"liabilities": liabilities, "holds": holds}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ("publication", "conditional-cases"))
@pytest.mark.parametrize(
    ("persist_progress", "expected_to_exist"),
    ((False, False), (True, True)),
)
async def test_task8_progress_cleanup_uses_explicit_persistence_policy(
    tmp_path, monkeypatch, failure_stage, persist_progress, expected_to_exist
):
    progress_path = tmp_path / "worker-qualification-progress.json"
    monkeypatch.setenv("P1_TASK8_PERSIST_FIXTURES", "1")

    async def publish_progress():
        progress_path.write_text("published", encoding="utf-8")
        if failure_stage == "publication":
            raise RuntimeError("injected producer failure")

    async def run_conditional_cases():
        assert progress_path.read_text(encoding="utf-8") == "published"
        if failure_stage == "conditional-cases":
            raise RuntimeError("injected conditional-case failure")

    with pytest.raises(RuntimeError, match="injected"):
        await _publish_task8_progress_and_run_cases(
            progress_path,
            publish_progress=publish_progress,
            run_conditional_cases=run_conditional_cases,
            persist_progress=persist_progress,
        )

    assert progress_path.exists() is expected_to_exist


def test_candidate_rollback_denial_harness_calls_launcher_and_detects_side_effects():
    calls = []
    candidate = {
        "image_id": "sha256:" + "d" * 64,
        "control_bundle": Path("/tmp/no-effects-control"),
        "fixture": {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "operation_ids": {
                "expired_permit_receipt": "00000000-0000-0000-0000-000000000002",
                "published_during_cancel": "00000000-0000-0000-0000-000000000003",
            },
        },
        "database_url": "postgresql://127.0.0.1/recovery_floor_active_0123456789ab",
    }

    def fake_invoke_launcher(**kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            args=["docker", "run"],
            returncode=78,
            stdout="",
            stderr=(
                "publication worker launch denied: "
                "both retained publication recovery holds failed verification"
            ),
        )

    result = _invoke_candidate_rollback_launcher(candidate, invoke=fake_invoke_launcher)
    assert result.returncode == 78
    assert calls[0]["image"] == candidate["image_id"]
    assert calls[0]["database_url"] == candidate["database_url"]
    assert calls[0]["argv"] == (
        "--mode",
        "rollback-restart",
        "--worker-kind",
        "legacy-fixture",
        "--workspace-id",
        candidate["fixture"]["workspace_id"],
        "--expired-hold-operation-id",
        candidate["fixture"]["operation_ids"]["expired_permit_receipt"],
        "--cancel-hold-operation-id",
        candidate["fixture"]["operation_ids"]["published_during_cancel"],
    )
    baseline = {
        "liabilities": {"expired_permit_receipt": {"job": 1}, "published_during_cancel": {"job": 2}},
        "holds": {"expired_permit_receipt": {"id": "hold-a"}, "published_during_cancel": {"id": "hold-b"}},
        "histories": {"expired_permit_receipt": [1, 2], "published_during_cancel": [3, 4]},
        "recorder_events": [],
    }
    _assert_candidate_launcher_denial_preserved_state(result, baseline, baseline)
    changed = json.loads(json.dumps(baseline))
    changed["liabilities"]["expired_permit_receipt"]["job"] = 9
    with pytest.raises(AssertionError):
        _assert_candidate_launcher_denial_preserved_state(result, baseline, changed)
    registered = json.loads(json.dumps(baseline))
    registered["histories"]["expired_permit_receipt"].append(5)
    with pytest.raises(AssertionError):
        _assert_candidate_launcher_denial_preserved_state(result, baseline, registered)
    recorder = json.loads(json.dumps(baseline))
    recorder["recorder_events"].append({"operation": "submit"})
    with pytest.raises(AssertionError):
        _assert_candidate_launcher_denial_preserved_state(result, baseline, recorder)


def test_candidate_read_only_harness_detects_mount_cache_and_manifest_failures():
    candidate = {
        "image_id": "sha256:" + "d" * 64,
        "image_ref": "synthetic-no-effects-worker:fixture",
        "database_url": "postgresql://127.0.0.1/recovery_floor_active_0123456789ab",
        "temporal_target": "127.0.0.1:7233",
        "control_bundle": Path("/tmp/no-effects-control"),
        "runtime_root": "/usr/local/lib/python3.13/site-packages",
        "progress": {
            "runtime_manifest_sha256": _canonical_json_hash(
                {"files": {"salience/worker.py": "a" * 64}, "pyc_files": []}
            )
        },
        "fixture": {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "operation_ids": {
                "expired_permit_receipt": "00000000-0000-0000-0000-000000000002",
                "published_during_cancel": "00000000-0000-0000-0000-000000000003",
            },
            "history_fixture": {
                reason: {"task_queue": "task8-fixture-" + reason}
                for reason in ("expired_permit_receipt", "published_during_cancel")
            },
        },
    }
    calls = []
    started = {}

    def image_manifest(image, root):
        calls.append(("image", image, root))
        return {"files": {"salience/worker.py": "a" * 64}, "pyc_files": []}

    def start_worker(**kwargs):
        calls.append(("start", kwargs))
        started.update(kwargs)
        argv = [
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            "/run/salience-control/launch-v4-publication-worker.py",
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "legacy-fixture",
            "--workspace-id",
            kwargs["workspace_id"],
            "--expired-hold-operation-id",
            kwargs["expired_operation_id"],
            "--cancel-hold-operation-id",
            kwargs["cancel_operation_id"],
        ]
        return {
            "container_name": "task8-readonly-test",
            "image_id": kwargs["image"],
            "launcher_argv": json.dumps(argv, separators=(",", ":")),
        }

    def inspect_worker(name):
        calls.append(("inspect", name))
        return {
            "image_id": candidate["image_id"],
            "path": "/usr/local/bin/python",
            "args": [
                "-I",
                "-B",
                "-S",
                "/run/salience-control/launch-v4-publication-worker.py",
                "--mode",
                "rollback-restart",
                "--worker-kind",
                "legacy-fixture",
                "--workspace-id",
                candidate["fixture"]["workspace_id"],
                "--expired-hold-operation-id",
                candidate["fixture"]["operation_ids"]["expired_permit_receipt"],
                "--cancel-hold-operation-id",
                candidate["fixture"]["operation_ids"]["published_during_cancel"],
            ],
            "environment": {
                "SALIENCE_DEPLOYMENT_MODE": "fixture",
                "SALIENCE_EFFECTS_ENABLED": "false",
                "TEST_TEMPORAL_TARGET": candidate["temporal_target"],
                "V4_LEGACY_FIXTURE_QUEUE": started["task_queue"],
                "V4_FIXTURE_WORKSPACE": candidate["fixture"]["workspace_id"],
            },
            "mounts": [
                {"destination": destination, "read_write": False}
                for destination in (
                    "/run/salience-control/launch-v4-publication-worker.py",
                    "/run/salience-control/publication-worker-launch-lock.json",
                    "/run/salience-control/publication-worker-launch-lock.sha256",
                )
            ],
            "state": "running",
            "read_only_rootfs": True,
        }

    def container_manifest(name, root):
        calls.append(("container-manifest", name, root))
        return {"files": {"salience/worker.py": "a" * 64}, "pyc_files": []}

    def stop_worker(name):
        calls.append(("stop", name))
        return {"state": "exited", "logs": "worker started"}

    result = _qualify_candidate_read_only_worker(
        candidate,
        image_manifest=image_manifest,
        start_worker=start_worker,
        inspect_worker=inspect_worker,
        container_manifest=container_manifest,
        stop_worker=stop_worker,
    )
    assert result["readonly_rootfs"] is True
    assert [item[0] for item in calls] == [
        "image", "start", "inspect", "container-manifest", "stop"
    ]
    good_inspection = inspect_worker("task8-readonly-test")
    writable_mount = {
        **good_inspection,
        "mounts": [
            {**good_inspection["mounts"][0], "read_write": True},
            *good_inspection["mounts"][1:],
        ],
    }
    for bad_inspection, bad_manifest in (
        ({**good_inspection, "read_only_rootfs": False}, {"files": {"salience/worker.py": "a" * 64}, "pyc_files": []}),
        (writable_mount, {"files": {"salience/worker.py": "a" * 64}, "pyc_files": []}),
        ({**good_inspection}, {"files": {"salience/worker.py": "b" * 64}, "pyc_files": []}),
        ({**good_inspection}, {"files": {}, "pyc_files": ["salience/__pycache__/worker.pyc"]}),
        ({**good_inspection, "args": ["-m", "salience.workflows.worker"]}, {"files": {"salience/worker.py": "a" * 64}, "pyc_files": []}),
    ):
        with pytest.raises(AssertionError):
            _qualify_candidate_read_only_worker(
                candidate,
                image_manifest=image_manifest,
                start_worker=start_worker,
                inspect_worker=lambda _name, value=bad_inspection: value,
                container_manifest=lambda _name, _root, value=bad_manifest: value,
                stop_worker=stop_worker,
            )


def _task8_rollback_image_or_skip(
    *, candidate_root=None, environment=None, require_legacy_null=False
):
    """Load the exact Task 8 image and persistent disposable rollback fixture.

    Task 8 writes the strict qualification progress schema under the fixed 0043
    candidate root. It contains target identity and the two operation/history
    IDs, never a database URL or credential. Runtime environment URLs are
    accepted only when their database name, host, and port match those recorded
    disposable targets. A wholly absent image bundle skips; partial or
    mismatched image state fails closed. Persisted-fixture selectors also skip
    when qualification progress has not been produced or has been archived.
    """
    import ipaddress
    import re
    from urllib.parse import urlsplit

    uses_default_candidate = candidate_root is None
    if candidate_root is None:
        candidate_root = (
            ROOT / "artifacts/v4-next-p1/agents/p1-publication-recovery-0043"
        )
    candidate_root = Path(candidate_root)
    if uses_default_candidate:
        prerequisites_path = (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/v4_publication_candidate_prerequisites.py"
        )
        prerequisites_spec = importlib.util.spec_from_file_location(
            "v4_publication_candidate_prerequisites", prerequisites_path
        )
        assert prerequisites_spec is not None and prerequisites_spec.loader is not None
        prerequisites_module = importlib.util.module_from_spec(prerequisites_spec)
        prerequisites_spec.loader.exec_module(prerequisites_module)
        exact_state = prerequisites_module.load_candidate_state(
            candidate_root=candidate_root,
            tracked_lock_path=ROOT / "deployment/v4/publication-worker-launch-lock.json",
            tracked_launcher_path=ROOT / "scripts/launch-v4-publication-worker.py",
            runtime_prefix=(
                Path("/tmp/p1-publication-recovery-0043/runtime-prefix")
                if Path(ROOT).resolve() == Path(__file__).resolve().parents[2]
                else Path(ROOT) / "runtime-prefix"
            ),
        )
        if exact_state is None:
            pytest.skip("Task 8 candidate fixture metadata is absent: candidate state is wholly absent")
        progress_path = candidate_root / "worker-qualification-progress.json"
        if not progress_path.exists() and not progress_path.is_symlink():
            pytest.skip(
                "Task 8 persistent qualification progress is absent; exact image state remains available"
            )
    legacy_candidate_root = (
        ROOT / "artifacts/v4-next-p1/agents/task-10-floor-qualification"
    )
    resolved_candidate_root = candidate_root.resolve(strict=False)
    resolved_legacy_root = legacy_candidate_root.resolve(strict=False)
    if (
        resolved_candidate_root == resolved_legacy_root
        or resolved_legacy_root in resolved_candidate_root.parents
    ):
        raise AssertionError("legacy Task 10 candidate provenance is rejected")
    environment = os.environ if environment is None else environment
    control_bundle = candidate_root / "control-bundle"
    metadata_paths = [
        candidate_root / "image-attestation.json",
        control_bundle / "launch-v4-publication-worker.py",
        control_bundle / "publication-worker-launch-lock.json",
        control_bundle / "publication-worker-launch-lock.sha256",
        candidate_root / "worker-qualification-progress.json",
        candidate_root / "worker-image-id.txt",
    ]
    missing = [path for path in metadata_paths if not path.is_file()]
    if missing:
        relative = [str(path.relative_to(candidate_root)) for path in missing]
        raise AssertionError(
            "partial Task 8 candidate fixture metadata: " + ", ".join(relative)
        )
    assert not candidate_root.is_symlink(), "candidate root must not be a symlink"
    assert not control_bundle.is_symlink(), "candidate control bundle must not be a symlink"
    assert {path.name for path in control_bundle.iterdir()} == {
        "launch-v4-publication-worker.py",
        "publication-worker-launch-lock.json",
        "publication-worker-launch-lock.sha256",
    }, "candidate control bundle must contain exactly the fixed launcher and lock files"

    def read_regular(path):
        metadata = path.lstat()
        assert not path.is_symlink() and metadata.st_mode & 0o170000 == 0o100000, (
            f"candidate metadata file is not regular: {path.name}"
        )
        return path.read_bytes()

    def read_json(path):
        return json.loads(read_regular(path))

    launcher_path, lock_path, digest_path = metadata_paths[1:4]
    launcher_bytes = read_regular(launcher_path)
    lock_bytes = read_regular(lock_path)
    digest_bytes = read_regular(digest_path)
    lock = json.loads(lock_bytes)
    assert lock_bytes == (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert lock.get("schema_version") == "salience.publication-worker-launch-lock.v1"
    pin = lock.get("pin")
    assert isinstance(pin, dict)
    assert pin.get("schema_version") == "salience.publication-worker-pin.v1"
    assert re.fullmatch(r"[0-9a-f]{40}", pin.get("application_commit", ""))
    assert pin.get("eligible_anchor") == ANCHOR
    assert lock.get("interpreter_path") == "/usr/local/bin/python"
    assert re.fullmatch(r"[0-9a-f]{64}", lock.get("interpreter_sha256", ""))
    assert lock.get("runtime_root") == "/usr/local/lib/python3.13/site-packages"
    assert lock.get("workers") == {
        "deployable": {"module": "salience.workflows.worker", "argv": []},
        "legacy-fixture": {"module": "salience.cycles.legacy_runtime", "argv": []},
    }
    lock_digest = _sha256(lock_bytes)
    assert digest_bytes == (lock_digest + "\n").encode("ascii")
    assert lock.get("launcher_sha256") == _sha256(launcher_bytes)
    assert pin.get("artifact_path") == "/opt/salience-artifacts/salience.whl"
    assert re.fullmatch(r"[0-9a-f]{64}", pin.get("artifact_sha256", ""))
    assert re.fullmatch(r"[0-9a-f]{64}", pin.get("distribution_record_sha256", ""))
    assert pin.get("distribution_record_path") == (
        "/usr/local/lib/python3.13/site-packages/salience-0.1.0.dist-info/RECORD"
    )
    source_files = pin.get("source_files")
    assert isinstance(source_files, list) and source_files
    assert all(
        isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and not PurePosixPath(item.get("path", "")).is_absolute()
        and ".." not in PurePosixPath(item.get("path", "")).parts
        and re.fullmatch(r"[0-9a-f]{40}", item.get("git_blob", ""))
        and re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
        for item in source_files
    )
    assert len({item["path"] for item in source_files}) == len(source_files)
    assert pin.get("source_manifest_sha256") == _canonical_json_hash(source_files)
    launcher_pins = [
        item for item in source_files if item.get("path") == "scripts/launch-v4-publication-worker.py"
    ]
    assert len(launcher_pins) == 1
    launcher_pin = launcher_pins[0]
    assert launcher_pin.get("sha256") == lock["launcher_sha256"]
    import_pins = pin.get("runtime_imports")
    required_imports = {
        "salience.cycles.publication_recovery_compatibility",
        "salience.cycles.legacy_runtime",
        "salience.cycles.legacy_publication_runtime",
        "salience.cycles.publication_recovery",
        "salience.cycles.legacy_publication",
        "salience.workflows.worker",
        "salience.workflows.publication",
        "salience.workflows.persistence",
        "salience.publication.repository",
    }
    assert isinstance(import_pins, list)
    assert len(import_pins) == len(required_imports)
    assert {item.get("module") for item in import_pins if isinstance(item, dict)} == required_imports
    for item in import_pins:
        module = item["module"]
        expected_wheel_path = module.replace(".", "/") + ".py"
        assert item.get("wheel_path") == expected_wheel_path
        assert item.get("observed_path") == lock["runtime_root"] + "/" + expected_wheel_path
        assert re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))

    attestation = read_json(candidate_root / "image-attestation.json")
    image_id = attestation.get("image_id")
    saved_image_id = read_regular(candidate_root / "worker-image-id.txt").decode(
        "ascii"
    ).strip()
    assert saved_image_id == image_id
    image_ref = f"salience-p1-floor-{pin['application_commit'][:7]}-worker:latest"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
    assert attestation.get("application_commit") == pin["application_commit"]
    assert attestation.get("image_ref") == image_ref
    assert attestation.get("image_id") == image_id
    assert attestation.get("wheel_sha256") == pin["artifact_sha256"]
    assert attestation.get("record_sha256") == pin["distribution_record_sha256"]
    assert attestation.get("record_ok") is True
    assert attestation.get("pyc_files") == []
    assert attestation.get("source_tree_loaded") is False
    assert attestation.get("runtime_root") == lock.get("runtime_root")
    assert re.fullmatch(
        r"[0-9a-f]{64}", attestation.get("runtime_manifest_sha256", "")
    )

    progress = read_json(candidate_root / "worker-qualification-progress.json")
    progress_fields = _load_worker_harness().QUALIFICATION_PROGRESS_FIELDS
    assert set(progress) == progress_fields, "Task 8 progress schema fields must match exactly"
    assert progress.get("schema_version") == "salience.publication-floor-qualification.v1"
    assert progress.get("launch_lock_sha256") == lock_digest
    assert progress.get("launcher_sha256") == lock["launcher_sha256"]
    assert progress.get("interpreter") == lock["interpreter_path"]
    assert progress.get("interpreter_sha256") == lock["interpreter_sha256"]
    assert progress.get("wheel_sha256") == pin["artifact_sha256"]
    assert progress.get("record_sha256") == pin["distribution_record_sha256"]
    assert progress.get("source_manifest_sha256") == pin["source_manifest_sha256"]
    assert progress.get("source_files") == source_files
    assert progress.get("runtime_imports") == import_pins
    assert progress.get("runtime_manifest_sha256") == attestation["runtime_manifest_sha256"]
    fixture = progress.get("rollback_launch_fixture")
    assert isinstance(fixture, dict), "Task 8 both-reason rollback history fixture metadata is required"
    assert set(fixture) == _load_worker_harness()._ROLLBACK_FIXTURE_FIELDS
    assert fixture.get("schema_version") == "salience.publication-floor-rollback-launch-fixture.v1"
    for item in (progress, fixture):
        assert item.get("application_commit") == pin["application_commit"]
        assert item.get("image_ref") == image_ref
        assert item.get("image_id") == image_id
    assert progress.get("runtime_root") == lock["runtime_root"]
    assert progress.get("source_tree_loaded") is False
    assert progress.get("effects_enabled") is False
    assert progress.get("production_effects_enabled") is False
    assert progress.get("fixture_publication_budget") == 0
    ancestry = progress.get("application_ancestry")
    assert isinstance(ancestry, dict)
    assert ancestry.get("anchor") == ANCHOR
    assert ancestry.get("candidate") == pin["application_commit"]
    assert ancestry.get("is_ancestor") is True
    assert fixture.get("launch_lock_sha256") == lock_digest
    reasons = {"expired_permit_receipt", "published_during_cancel"}
    workspace_id = str(UUID(fixture["workspace_id"]))
    operation_ids = fixture.get("operation_ids")
    hold_ids = fixture.get("hold_ids")
    assert isinstance(operation_ids, dict) and set(operation_ids) == reasons
    assert isinstance(hold_ids, dict) and set(hold_ids) == reasons
    assert len({str(UUID(value)) for value in operation_ids.values()}) == 2
    assert len({str(UUID(value)) for value in hold_ids.values()}) == 2
    assert progress.get("workspace_id") == workspace_id
    assert progress.get("operation_ids") == operation_ids
    assert progress.get("hold_ids") == hold_ids
    histories = fixture.get("history_fixture")
    assert isinstance(histories, dict) and set(histories) == reasons
    for reason in reasons:
        history = histories[reason]
        assert set(history) == {"workflow_id", "task_queue", "last_event_id", "history_sha256"}
        assert history.get("workflow_id") == (
            "salience-v4-legacy-operation:" + str(UUID(operation_ids[reason]))
        )
        assert isinstance(history.get("task_queue"), str) and history["task_queue"]
        assert isinstance(history.get("last_event_id"), int) and history["last_event_id"] > 0
        assert re.fullmatch(r"[0-9a-f]{64}", history.get("history_sha256", ""))
    for field in ("liability_snapshot_hashes", "hold_row_hashes"):
        hashes = fixture.get(field)
        assert isinstance(hashes, dict) and set(hashes) == reasons
        assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values())

    targets = fixture.get("disposable_targets")
    assert isinstance(targets, dict)
    assert set(targets) == {
        "database_name",
        "database_host",
        "database_port",
        "temporal_target",
        "adapter_recorder",
        "fixture_lifetime",
    }
    database_url = environment.get("TEST_DATABASE_URL")
    temporal_target = environment.get("TEST_TEMPORAL_TARGET")
    assert database_url, "TEST_DATABASE_URL must target the Task 8 disposable database"
    assert temporal_target, "TEST_TEMPORAL_TARGET must target the Task 8 disposable Temporal"
    database_parts = urlsplit(database_url)
    database_name = database_parts.path.lstrip("/")
    database_host = database_parts.hostname
    database_port = database_parts.port or 5432
    assert database_parts.scheme in {"postgresql", "postgres"}
    assert database_host
    assert re.fullmatch(r"recovery_floor_active_[0-9a-f]{12}", database_name)
    assert database_name == targets.get("database_name")
    assert database_host == targets.get("database_host")
    assert database_port == targets.get("database_port")
    temporal_parts = urlsplit("//" + temporal_target)
    assert temporal_parts.hostname and temporal_parts.port
    assert temporal_parts.username is None and temporal_parts.password is None
    assert temporal_parts.path == "" and temporal_parts.query == "" and temporal_parts.fragment == ""
    assert temporal_target == targets.get("temporal_target")

    def require_disposable_host(host, *, allow_service_name=False):
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if allow_service_name:
                assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", host), (
                    "candidate recorder target must use a local container service name"
                )
            else:
                assert host in {"localhost", "postgres", "temporal"}, (
                    "candidate fixture target must use a local test service"
                )
        else:
            assert address.is_private or address.is_loopback, (
                "candidate fixture target must use a private or loopback address"
            )

    require_disposable_host(database_host)
    require_disposable_host(temporal_parts.hostname)
    recorder = targets.get("adapter_recorder")
    assert isinstance(recorder, dict)
    assert set(recorder) == {"base_url", "run_id"}
    recorder_url = urlsplit(recorder.get("base_url", ""))
    assert recorder_url.scheme == "http" and recorder_url.hostname
    require_disposable_host(recorder_url.hostname, allow_service_name=True)
    recorder_run_id = str(UUID(recorder["run_id"]))
    assert recorder_url.username is None and recorder_url.password is None
    assert recorder_url.path in {"", "/"}
    assert not recorder_url.query and not recorder_url.fragment
    assert targets.get("fixture_lifetime") == "task8-persistent"

    legacy_fixture = progress.get("legacy_null_v1_fixture")
    assert isinstance(legacy_fixture, dict)
    assert set(legacy_fixture) == _load_worker_harness()._LEGACY_NULL_FIXTURE_FIELDS
    assert legacy_fixture.get("schema_version") == "salience.publication-floor-legacy-null-v1-fixture.v1"
    assert legacy_fixture.get("application_commit") == pin["application_commit"]
    assert legacy_fixture.get("image_ref") == image_ref
    assert legacy_fixture.get("image_id") == image_id
    assert legacy_fixture.get("launch_lock_sha256") == lock_digest
    legacy_operation_ids = legacy_fixture.get("operation_ids")
    legacy_hold_ids = legacy_fixture.get("hold_ids")
    assert isinstance(legacy_operation_ids, dict) and set(legacy_operation_ids) == reasons
    assert isinstance(legacy_hold_ids, dict) and set(legacy_hold_ids) == reasons
    assert len({str(UUID(value)) for value in legacy_operation_ids.values()}) == 2
    assert len({str(UUID(value)) for value in legacy_hold_ids.values()}) == 2
    legacy_null_operation_id = str(UUID(legacy_fixture["legacy_null_operation_id"]))
    assert legacy_null_operation_id == str(
        UUID(legacy_operation_ids["published_during_cancel"])
    )
    assert legacy_null_operation_id not in {str(UUID(value)) for value in operation_ids.values()}
    assert legacy_fixture.get("execution_mode") == {
        "mode": "preflight_only",
        "denial_stage": "before_worker_registration",
        "worker_started": False,
        "outbox_dispatched": False,
    }
    assert "history_fixture" not in legacy_fixture, (
        "legacy NULL-v1 preflight evidence must not claim Temporal replay metadata"
    )
    for field in ("liability_snapshot_hashes", "hold_row_hashes"):
        hashes = legacy_fixture.get(field)
        assert isinstance(hashes, dict) and set(hashes) == reasons
        assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values())
    legacy_targets = legacy_fixture.get("disposable_targets")
    assert isinstance(legacy_targets, dict)
    assert set(legacy_targets) == set(targets)
    assert legacy_targets.get("database_name") != targets.get("database_name")
    assert legacy_targets.get("fixture_lifetime") == "task8-persistent"
    assert legacy_targets.get("temporal_target") == temporal_target
    assert legacy_targets.get("adapter_recorder") == recorder
    legacy_database_url = environment.get("TEST_LEGACY_NULL_DATABASE_URL")
    assert legacy_database_url, "TEST_LEGACY_NULL_DATABASE_URL must target the legacy NULL-v1 fixture"
    legacy_database_parts = urlsplit(legacy_database_url)
    legacy_database_name = legacy_database_parts.path.lstrip("/")
    assert legacy_database_parts.scheme in {"postgresql", "postgres"}
    assert legacy_database_parts.hostname == legacy_targets.get("database_host")
    assert (legacy_database_parts.port or 5432) == legacy_targets.get("database_port")
    assert re.fullmatch(r"recovery_floor_legacy_[0-9a-f]{12}", legacy_database_name)
    assert legacy_database_name == legacy_targets.get("database_name")
    require_disposable_host(legacy_database_parts.hostname)
    assert not legacy_database_parts.query and not legacy_database_parts.fragment
    if require_legacy_null:
        assert legacy_fixture

    return {
        "candidate_root": candidate_root,
        "control_bundle": control_bundle,
        "image_ref": image_ref,
        "image_id": image_id,
        "runtime_root": lock["runtime_root"],
        "lock": lock,
        "attestation": attestation,
        "progress": progress,
        "fixture": fixture,
        "legacy_null_fixture": legacy_fixture,
        "database_url": database_url,
        "temporal_target": temporal_target,
        "adapter_recorder_url": recorder_url.geturl().rstrip("/"),
        "adapter_recorder_run_id": recorder_run_id,
        "legacy_null_database_url": legacy_database_url,
    }


def _invoke_candidate_rollback_launcher(candidate, *, invoke=None):
    if invoke is None:
        invoke = _load_worker_harness().invoke_launcher
    fixture = candidate["fixture"]
    operation_ids = fixture["operation_ids"]
    argv = (
        "--mode",
        "rollback-restart",
        "--worker-kind",
        "legacy-fixture",
        "--workspace-id",
        str(UUID(fixture["workspace_id"])),
        "--expired-hold-operation-id",
        str(UUID(operation_ids["expired_permit_receipt"])),
        "--cancel-hold-operation-id",
        str(UUID(operation_ids["published_during_cancel"])),
    )
    return invoke(
        image=candidate["image_id"],
        database_url=candidate["database_url"],
        control_bundle=candidate["control_bundle"],
        argv=argv,
    )


def _assert_candidate_launcher_denial_preserved_state(result, before, after):
    assert result.returncode == 78, result.stderr
    detail = result.stderr.casefold()
    assert detail.startswith("publication worker launch denied: ")
    assert "both retained publication recovery holds failed verification" in detail
    assert after == before
    assert before["recorder_events"] == []
    assert after["recorder_events"] == []


def _assert_only_liability_collection_changed(before, after, collection):
    assert set(before) == set(after)
    assert before[collection] != after[collection]
    assert all(before[key] == after[key] for key in before if key != collection)


def _assert_candidate_mutation_scope(before, after, reason, collection):
    _assert_only_liability_collection_changed(
        before["liabilities"][reason], after["liabilities"][reason], collection
    )
    other_reason = (
        "expired_permit_receipt"
        if reason == "published_during_cancel"
        else "published_during_cancel"
    )
    assert before["liabilities"][other_reason] == after["liabilities"][other_reason]
    assert after["holds"] == before["holds"]
    assert after["histories"] == before["histories"]
    assert after["recorder_events"] == before["recorder_events"] == []


def _qualify_candidate_read_only_worker(
    candidate,
    *,
    image_manifest,
    start_worker,
    inspect_worker,
    container_manifest,
    stop_worker,
):
    before = image_manifest(candidate["image_id"], candidate["runtime_root"])
    assert before["pyc_files"] == []
    assert _canonical_json_hash(before) == candidate["progress"]["runtime_manifest_sha256"]
    fixture = candidate["fixture"]
    operation_ids = fixture["operation_ids"]
    from uuid import uuid4

    name = "task8-readonly-" + uuid4().hex[:12]
    task_queue = "task8-readonly-" + uuid4().hex
    launch = None
    try:
        launch = start_worker(
            image=candidate["image_id"],
            database_url=candidate["database_url"],
            temporal_target=candidate["temporal_target"],
            workspace_id=fixture["workspace_id"],
            expired_operation_id=operation_ids["expired_permit_receipt"],
            cancel_operation_id=operation_ids["published_during_cancel"],
            task_queue=task_queue,
            control_bundle=candidate["control_bundle"],
            name=name,
        )
        inspection = inspect_worker(name)
        expected_container_argv = [
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            "/run/salience-control/launch-v4-publication-worker.py",
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "legacy-fixture",
            "--workspace-id",
            fixture["workspace_id"],
            "--expired-hold-operation-id",
            operation_ids["expired_permit_receipt"],
            "--cancel-hold-operation-id",
            operation_ids["published_during_cancel"],
        ]
        assert launch["image_id"] == candidate["image_id"]
        assert launch["launcher_argv"] == json.dumps(
            expected_container_argv, separators=(",", ":")
        )
        assert inspection["image_id"] == candidate["image_id"]
        assert inspection["path"] == expected_container_argv[0]
        assert inspection["args"] == expected_container_argv[1:]
        assert inspection["read_only_rootfs"] is True
        assert inspection["state"] == "running"
        assert inspection["environment"] == {
            "SALIENCE_DEPLOYMENT_MODE": "fixture",
            "SALIENCE_EFFECTS_ENABLED": "false",
            "TEST_TEMPORAL_TARGET": candidate["temporal_target"],
            "V4_LEGACY_FIXTURE_QUEUE": task_queue,
            "V4_FIXTURE_WORKSPACE": fixture["workspace_id"],
        }
        assert {
            item["destination"] for item in inspection["mounts"]
        } == {
            "/run/salience-control/launch-v4-publication-worker.py",
            "/run/salience-control/publication-worker-launch-lock.json",
            "/run/salience-control/publication-worker-launch-lock.sha256",
        }
        assert all(item["read_write"] is False for item in inspection["mounts"])
        after = container_manifest(name, candidate["runtime_root"])
        assert after["pyc_files"] == []
        assert after["files"] == before["files"]
        assert _canonical_json_hash(after) == candidate["progress"]["runtime_manifest_sha256"]
        return {
            "launch": launch,
            "inspection": inspection,
            "manifest": after,
            "readonly_rootfs": inspection["read_only_rootfs"],
        }
    finally:
        stopped = stop_worker(name)
        if launch is None:
            assert stopped["state"] in {"exited", "already-removed"}
        else:
            assert stopped["state"] == "exited"


def _assert_candidate_image(candidate, image_inspector=None):
    if image_inspector is None:
        image_inspector = _load_worker_harness().image_id
    assert image_inspector(candidate["image_ref"]) == candidate["image_id"]


def _adapter_recorder_events(candidate):
    return _read_adapter_recorder_events(
        candidate["adapter_recorder_url"], candidate["adapter_recorder_run_id"]
    )


def _read_adapter_recorder_events(base_url, run_id):
    import urllib.request

    url = base_url.rstrip("/") + "/v1/runs/" + run_id + "/events"
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read())
    assert payload["run_id"] == run_id
    return payload["events"]


def _assert_adapter_operation_events(events, *, run_id, operations):
    expected_operations = tuple(operations)
    assert len(events) == len(expected_operations)
    worker_ids = set()
    for sequence, (event, operation) in enumerate(
        zip(events, expected_operations, strict=True), start=1
    ):
        assert set(event) == {
            "schema",
            "run_id",
            "worker_id",
            "sequence",
            "operation",
        }
        assert event["schema"] == "salience.adapter-operation.v1"
        assert event["run_id"] == run_id
        assert str(UUID(event["worker_id"])) == event["worker_id"]
        worker_ids.add(event["worker_id"])
        assert event["sequence"] == sequence
        assert event["operation"] == operation
    assert len(worker_ids) == 1


def _assert_no_adapter_operation_events(events):
    assert events == [], f"adapter recorder observed {len(events)} operation(s)"


def _load_adapter_operation_fixture(path, module_name):
    path = Path(path)
    if not path.is_file():
        pytest.fail(f"required adapter-operation fixture is missing: {path.name}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        pytest.fail(f"adapter-operation fixture cannot be loaded: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _candidate_history_fingerprints(candidate):
    from temporalio.client import Client

    client = await Client.connect(candidate["temporal_target"])
    result = {}
    for reason, metadata in candidate["fixture"]["history_fixture"].items():
        handle = client.get_workflow_handle(metadata["workflow_id"])
        history = await handle.fetch_history()
        last_event_id = max((event.event_id for event in history.events), default=0)
        result[reason] = {
            "workflow_id": metadata["workflow_id"],
            "task_queue": metadata["task_queue"],
            "last_event_id": last_event_id,
            "history_sha256": _temporal_history_sha256(history.events),
        }
    return result


def _temporal_history_sha256(events):
    """Fingerprint history independent of protobuf map insertion order."""
    return _sha256(
        b"".join(event.SerializeToString(deterministic=True) for event in events)
    )


def _task8_external_history_metadata(candidate):
    fixture = candidate["fixture"]
    histories = fixture.get("history_fixture")
    if candidate.get("database_target") == "legacy-null-v1":
        assert fixture.get("execution_mode") == {
            "mode": "preflight_only",
            "denial_stage": "before_worker_registration",
            "worker_started": False,
            "outbox_dispatched": False,
        }
        assert histories is None or histories == {}
        return None
    assert isinstance(histories, dict)
    assert set(histories) == {"expired_permit_receipt", "published_during_cancel"}
    assert all(isinstance(history, dict) for history in histories.values())
    return histories


def _canonical_json_hash(value):
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return _sha256(encoded)


def test_adapter_operation_recorder_emits_protocol_events_and_zero_assertion_is_sensitive():
    from threading import Thread
    from uuid import uuid4

    receiver = _load_adapter_operation_fixture(
        ROOT / "tests/fixtures/v4_publication_adapter_operation_receiver.py",
        "v4_publication_adapter_operation_receiver",
    )
    recorder = _load_adapter_operation_fixture(
        ROOT / "tests/fixtures/v4_publication_adapter_operation_recorder.py",
        "v4_publication_adapter_operation_recorder",
    )
    server = receiver.create_server("127.0.0.1", 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    run_id = str(uuid4())
    operations = (
        "capabilities",
        "preflight",
        "prepare_delivery",
        "create_or_resume",
        "submit",
        "reconcile",
        "status",
        "cancel",
        "verify_webhook",
        "refresh_capabilities",
    )
    try:
        asyncio.run(recorder.run_positive_control(base_url=base_url, run_id=run_id))
        events = _read_adapter_recorder_events(base_url, run_id)
        _assert_adapter_operation_events(events, run_id=run_id, operations=operations)

        # This positive control deliberately wires adapter methods to the
        # recorder. The same empty-store assertion used by held cases must
        # reject that intentionally recorded operation set.
        with pytest.raises(AssertionError, match="adapter recorder observed 10"):
            _assert_no_adapter_operation_events(events)

        sensitivity_run_id = str(uuid4())
        asyncio.run(
            recorder.run_single_operation(
                base_url=base_url,
                run_id=sensitivity_run_id,
            )
        )
        sensitivity_events = _read_adapter_recorder_events(base_url, sensitivity_run_id)
        _assert_adapter_operation_events(
            sensitivity_events,
            run_id=sensitivity_run_id,
            operations=("preflight",),
        )
        with pytest.raises(AssertionError, match="adapter recorder observed 1"):
            _assert_no_adapter_operation_events(sensitivity_events)

        empty_run_id = str(uuid4())
        empty_events = _read_adapter_recorder_events(base_url, empty_run_id)
        assert empty_events == []
        _assert_no_adapter_operation_events(empty_events)

        # Receiver reads are sequence ordered even if independent workers post
        # out of order. This pins the external-store contract separately from
        # the recorder's sequential emission behavior.
        from urllib.request import Request, urlopen

        ordered_run_id = str(uuid4())
        ordered_worker_id = str(uuid4())
        for sequence in (2, 1):
            event = {
                "schema": "salience.adapter-operation.v1",
                "run_id": ordered_run_id,
                "worker_id": ordered_worker_id,
                "sequence": sequence,
                "operation": "preflight",
            }
            request = Request(
                base_url + "/v1/adapter-events",
                data=(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                assert response.status == 202
        ordered_events = _read_adapter_recorder_events(base_url, ordered_run_id)
        assert [event["sequence"] for event in ordered_events] == [1, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cross_container_adapter_recorder_positive_control():
    from uuid import uuid4

    candidate = _task8_rollback_image_or_skip()
    _assert_candidate_image(candidate)
    worker_harness = _load_worker_harness()
    run_id = str(uuid4())
    _assert_no_adapter_operation_events(
        _read_adapter_recorder_events(candidate["adapter_recorder_url"], run_id)
    )
    worker_id = "task7-adapter-positive-" + uuid4().hex[:12]
    task_queue = "task7-adapter-positive-" + uuid4().hex
    launch = None
    try:
        launch = worker_harness.start_rollback_worker(
            image=candidate["image_id"],
            database_url=candidate["database_url"],
            temporal_target=candidate["temporal_target"],
            workspace_id=candidate["fixture"]["workspace_id"],
            expired_operation_id=candidate["fixture"]["operation_ids"][
                "expired_permit_receipt"
            ],
            cancel_operation_id=candidate["fixture"]["operation_ids"][
                "published_during_cancel"
            ],
            task_queue=task_queue,
            control_bundle=candidate["control_bundle"],
            name=worker_id,
        )
        inspection = worker_harness.inspect_worker(worker_id)
        assert launch["image_id"] == candidate["image_id"]
        assert inspection["image_id"] == candidate["image_id"]
        assert inspection["state"] == "running"
        assert inspection["read_only_rootfs"] is True
        assert inspection["environment"]["SALIENCE_EFFECTS_ENABLED"] == "false"
        result = worker_harness.run_adapter_operation_positive_control(
            worker_id,
            recorder_fixture=ROOT
            / "tests/fixtures/v4_publication_adapter_operation_recorder.py",
            receiver_url=candidate["adapter_recorder_url"],
            run_id=run_id,
        )
        assert result.returncode == 0, result.stderr
    finally:
        stopped = worker_harness.stop_worker(worker_id)
        if launch is None:
            assert stopped["state"] in {"exited", "already-removed"}
        else:
            assert stopped["state"] == "exited"

    operations = (
        "capabilities",
        "preflight",
        "prepare_delivery",
        "create_or_resume",
        "submit",
        "reconcile",
        "status",
        "cancel",
        "verify_webhook",
        "refresh_capabilities",
    )
    events = _read_adapter_recorder_events(candidate["adapter_recorder_url"], run_id)
    _assert_adapter_operation_events(events, run_id=run_id, operations=operations)
    with pytest.raises(AssertionError, match="adapter recorder observed 10"):
        _assert_no_adapter_operation_events(events)


@pytest.mark.parametrize(
    "reason",
    ("expired_permit_receipt", "published_during_cancel"),
    ids=("expired-permit-receipt", "published-during-cancel"),
)
@pytest.mark.asyncio
async def test_cross_container_adapter_recorder_observes_zero_operations_for_both_holds(
    reason,
):
    """Observe exact-image workflow replay after a persisted typed hold.

    The monolithic Task 8 rehearsal separately executes each pending activity
    retry through the exact image with a per-worker recorder. This reset starts
    after Temporal has recorded the typed activity failure, so replay must
    consume that history without scheduling or starting another activity.
    The independent receiver verifies that the replay makes no adapter calls.
    """
    import asyncio
    from uuid import uuid4

    from temporalio.client import Client, WorkflowFailureError
    from test_v4_legacy_publication import _exception_chain

    candidate = _task8_rollback_image_or_skip()
    _assert_candidate_image(candidate)
    database_url = candidate["database_url"]
    before = _capture_candidate_database_state(
        candidate, database_url=database_url
    )
    assert len(before["liabilities"][reason]) == 21
    recorder_run_id = str(uuid4())
    recorder_worker_id = str(uuid4())
    events_before = _read_adapter_recorder_events(
        candidate["adapter_recorder_url"], recorder_run_id
    )
    assert events_before == []
    _assert_no_adapter_operation_events(events_before)

    worker_harness = _load_worker_harness()
    fixture = candidate["fixture"]
    operation_ids = fixture["operation_ids"]
    task_queue = fixture["history_fixture"][reason]["task_queue"]
    workflow_id = fixture["history_fixture"][reason]["workflow_id"]

    def activity_schedule_and_start_event_ids(events):
        return {
            "scheduled": [
                event.event_id
                for event in events
                if event.HasField("activity_task_scheduled_event_attributes")
            ],
            "started": [
                event.event_id
                for event in events
                if event.HasField("activity_task_started_event_attributes")
            ],
        }

    client = await Client.connect(candidate["temporal_target"])
    handle = client.get_workflow_handle(workflow_id)
    history_before = await handle.fetch_history()
    strict_history = fixture["history_fixture"][reason]
    assert max(event.event_id for event in history_before.events) == strict_history[
        "last_event_id"
    ]
    assert _temporal_history_sha256(history_before.events) == strict_history[
        "history_sha256"
    ], "rollback-restart did not consume the exact history bound by strict progress"
    activity_events_before = activity_schedule_and_start_event_ids(
        history_before.events
    )
    worker_name = (
        "task7-adapter-rollback-restart-"
        + reason.replace("_", "-")
        + "-"
        + uuid4().hex[:8]
    )
    launch = None
    stopped = None
    worker_log_path = candidate["candidate_root"] / (
        f"task7-adapter-rollback-restart-{reason}-{recorder_run_id}.log"
    )
    try:
        launch = worker_harness.start_rollback_worker(
            image=candidate["image_id"],
            database_url=database_url,
            temporal_target=candidate["temporal_target"],
            workspace_id=fixture["workspace_id"],
            expired_operation_id=operation_ids["expired_permit_receipt"],
            cancel_operation_id=operation_ids["published_during_cancel"],
            task_queue=task_queue,
            control_bundle=candidate["control_bundle"],
            name=worker_name,
            adapter_recorder_url=candidate["adapter_recorder_url"],
            adapter_recorder_run_id=recorder_run_id,
            adapter_recorder_worker_id=recorder_worker_id,
        )
        inspection = worker_harness.inspect_worker(worker_name)
        assert launch["image_id"] == candidate["image_id"]
        assert inspection["image_id"] == candidate["image_id"]
        assert inspection["state"] == "running"
        assert inspection["read_only_rootfs"] is True
        assert inspection["environment"]["SALIENCE_EFFECTS_ENABLED"] == "false"

        with pytest.raises(WorkflowFailureError) as failure:
            await asyncio.wait_for(handle.result(), timeout=45)
        typed_holds = [
            error
            for error in _exception_chain(failure.value)
            if getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
        ]
        assert len(typed_holds) == 1
        assert typed_holds[0].details == ({"hold_id": fixture["hold_ids"][reason]},)
        history_after_run = await handle.fetch_history()
        activity_events_after = activity_schedule_and_start_event_ids(
            history_after_run.events
        )
        new_schedule_ids = set(activity_events_after["scheduled"]) - set(
            activity_events_before["scheduled"]
        )
        new_start_ids = set(activity_events_after["started"]) - set(
            activity_events_before["started"]
        )
        assert new_schedule_ids == set()
        assert new_start_ids == set()
        schedules_by_id = {
            event.event_id: event.activity_task_scheduled_event_attributes.activity_type.name
            for event in history_after_run.events
            if event.HasField("activity_task_scheduled_event_attributes")
        }
        expected_activity_type = {
            "expired_permit_receipt": "salience.publication.submit_or_reconcile",
            "published_during_cancel": "salience.publication.cancel",
        }[reason]
        matching_schedule_ids = {
            event_id
            for event_id, activity_type in schedules_by_id.items()
            if activity_type == expected_activity_type
        }
        assert len(matching_schedule_ids) == 1
        matching_start_ids = {
            event.event_id
            for event in history_after_run.events
            if event.HasField("activity_task_started_event_attributes")
            and event.activity_task_started_event_attributes.scheduled_event_id
            in matching_schedule_ids
        }
        assert len(matching_start_ids) == 1
    finally:
        stopped = worker_harness.stop_worker(worker_name)
        worker_log = str(stopped.get("logs", "")).replace(
            database_url, "<redacted-database-url>"
        )
        worker_log_path.write_text(worker_log, encoding="utf-8")
        if launch is None:
            assert stopped["state"] in {"exited", "already-removed"}
        else:
            assert stopped["state"] == "exited"

    after = _capture_candidate_database_state(
        candidate, database_url=database_url, validate_metadata_baseline=False
    )
    events_after = _read_adapter_recorder_events(
        candidate["adapter_recorder_url"], recorder_run_id
    )
    recorder_evidence = {
        "schema_version": "salience.task7-adapter-recorder-replay.v1",
        "reason": reason,
        "workflow_id": workflow_id,
        "run_id": (await handle.describe()).run_id,
        "recorder_run_id": recorder_run_id,
        "recorder_worker_id": recorder_worker_id,
        "worker_container_name": worker_name,
        "worker_container_id": launch["container_id"] if launch else None,
        "worker_image_id": launch["image_id"] if launch else None,
        "worker_exit_state": stopped.get("state") if stopped else None,
        "worker_log_path": str(worker_log_path.relative_to(candidate["candidate_root"])),
        "worker_log_sha256": _sha256(worker_log.encode()),
        "strict_pre_run_history": strict_history,
        "post_run_history_sha256": _temporal_history_sha256(history_after_run.events),
        "activity_schedule_and_start_event_ids_before": activity_events_before,
        "activity_schedule_and_start_event_ids_after": activity_events_after,
        "restarted_activity_type": expected_activity_type,
        "database_state_before": before,
        "database_state_after": after,
        "database_state_unchanged": after == before,
        "recorder_events_before": events_before,
        "recorder_events_after": events_after,
    }
    evidence_path = candidate["candidate_root"] / (
        f"task7-adapter-recorder-replay-{reason}-{recorder_run_id}.json"
    )
    evidence_path.write_text(
        json.dumps(recorder_evidence, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    assert after == before, recorder_evidence
    assert after["liabilities"][reason] == before["liabilities"][reason]
    assert after["holds"] == before["holds"]
    assert events_after == events_before
    _assert_no_adapter_operation_events(events_after)
    return {
        "reason": reason,
        "workflow_id": workflow_id,
        "run_id": recorder_evidence["run_id"],
        "strict_pre_run_history_sha256": strict_history["history_sha256"],
        "post_run_history_sha256": _temporal_history_sha256(history_after_run.events),
        "activity_schedule_and_start_event_ids_before": activity_events_before,
        "activity_schedule_and_start_event_ids_after": activity_events_after,
        "restarted_activity_type": expected_activity_type,
        "new_activity_schedule_or_start_events": len(new_schedule_ids) + len(new_start_ids),
        "recorder_run_id": recorder_run_id,
        "recorder_worker_id": recorder_worker_id,
        "recorder_events_before": events_before,
        "recorder_events_after": events_after,
        "typed_hold": typed_holds[0].type,
        "worker_container_id": launch["container_id"],
        "worker_image_id": launch["image_id"],
        "worker_exit_state": stopped["state"],
        "worker_log_path": str(worker_log_path.relative_to(candidate["candidate_root"])),
        "evidence_path": str(evidence_path.relative_to(candidate["candidate_root"])),
    }


async def _publish_task8_progress_and_run_cases(
    progress_path, *, publish_progress, run_conditional_cases, persist_progress=False
):
    """Keep strict progress only while its disposable targets remain available."""
    try:
        await publish_progress()
        await run_conditional_cases()
    finally:
        if not persist_progress:
            Path(progress_path).unlink(missing_ok=True)


def _capture_candidate_database_state(
    candidate, *, database_url, validate_metadata_baseline=True
):
    import psycopg
    from test_v4_publication_recovery_holds import (
        _liability_snapshot,
        _recovery_hold_snapshot,
    )

    assert database_url == candidate["database_url"], (
        "database snapshot target must match the explicitly selected candidate URL"
    )
    with psycopg.connect(database_url) as connection:
        head = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        assert head == [("0043_complete_publication_recovery_liabilities",)]
    fixture = candidate["fixture"]
    liabilities = {
        reason: _liability_snapshot(database_url, operation_id)
        for reason, operation_id in fixture["operation_ids"].items()
    }
    holds = {
        reason: _recovery_hold_snapshot(database_url, operation_id)
        for reason, operation_id in fixture["operation_ids"].items()
    }
    assert all(row is not None for row in holds.values())
    for reason, operation_id in fixture["operation_ids"].items():
        hold = holds[reason]
        assert str(UUID(hold["id"])) == str(UUID(fixture["hold_ids"][reason]))
        assert str(UUID(hold["operation_id"])) == str(UUID(operation_id))
        assert str(UUID(hold["workspace_id"])) == str(UUID(fixture["workspace_id"]))
        assert hold["reason"] == reason
    if validate_metadata_baseline:
        for reason in fixture["operation_ids"]:
            assert _canonical_json_hash(liabilities[reason]) == fixture[
                "liability_snapshot_hashes"
            ][reason]
            assert _canonical_json_hash(holds[reason]) == fixture["hold_row_hashes"][reason]
    return {"liabilities": liabilities, "holds": holds}


async def _capture_candidate_external_state(
    candidate, *, database_url, validate_metadata_baseline=True
):
    database = _capture_candidate_database_state(
        candidate,
        database_url=database_url,
        validate_metadata_baseline=validate_metadata_baseline,
    )
    expected_histories = _task8_external_history_metadata(candidate)
    if expected_histories is None:
        histories = {}
    else:
        histories = await _candidate_history_fingerprints(candidate)
        assert histories == expected_histories
    events = _adapter_recorder_events(candidate)
    assert events == []
    return {**database, "histories": histories, "recorder_events": events}


def _mutate_omitted_liability_collection(
    database_url, snapshot, collection, *, mutation=None, target_id=None
):
    import psycopg
    from uuid import UUID, uuid4

    by_label = {
        "remote-publication-receipts": "remote_publication_receipts",
        "job-checkpoints": "job_checkpoints",
        "audit-events": "audit_events",
        "provenance-records": "provenance_records",
    }
    assert collection in by_label
    operation = by_label[collection]
    marker = "recovery-mutation-" + uuid4().hex
    job = snapshot["job"]
    attempt = snapshot["publication_attempt"]
    workspace_id = job["workspace_id"]
    job_id = job["id"]
    inserted_id = uuid4() if target_id is None else UUID(str(target_id))
    action = mutation
    if action is None:
        action = "append"
    if action not in {"append", "update", "delete"}:
        raise ValueError("mutation must be append, update, or delete")
    if action in {"update", "delete"}:
        assert target_id is not None, f"{action} requires a target row ID"
        assert str(target_id) in {
            str(row["id"]) for row in snapshot[operation]
        }, f"target row must belong to the selected liability collection: {collection}"
        if operation == "remote_publication_receipts":
            raise ValueError("remote publication receipts are immutable")

    restore_values = None
    with psycopg.connect(database_url) as connection:
        if action == "update":
            column = {
                "job_checkpoints": "checkpoint_payload",
                "audit_events": "details",
                "provenance_records": "lineage",
            }[operation]
            old_value = connection.execute(
                f"SELECT {column} FROM {operation} WHERE id=%s", (target_id,)
            ).fetchone()
            assert old_value is not None
            updated_value = {
                "task4_mutation": marker,
                "previous_value": old_value[0],
            }
            connection.execute(
                f"UPDATE {operation} SET {column}=%s::jsonb WHERE id=%s",
                (json.dumps(updated_value), target_id),
            )
            restore_values = ("update", operation, target_id, column, old_value[0])
        elif action == "delete":
            row = connection.execute(
                f"SELECT to_jsonb(liability_row) FROM {operation} liability_row WHERE id=%s",
                (target_id,),
            ).fetchone()
            assert row is not None
            connection.execute(f"DELETE FROM {operation} WHERE id=%s", (target_id,))
            restore_values = ("reinsert", operation, target_id, row[0])
        elif operation == "remote_publication_receipts" and mutation is None:
            row = connection.execute(
                "SELECT id, disclosure_projection FROM remote_publication_receipts "
                "WHERE publication_attempt_id=%s ORDER BY id LIMIT 1",
                (attempt["id"],),
            ).fetchone()
            if row:
                old_value = row[1]
                new_value = (
                    {**old_value, "task4_mutation": marker}
                    if isinstance(old_value, dict)
                    else {"task4_mutation": marker, "prior_value": old_value}
                )
                connection.execute(
                    "ALTER TABLE public.remote_publication_receipts DISABLE TRIGGER USER"
                )
                connection.execute(
                    "UPDATE remote_publication_receipts SET disclosure_projection=%s::jsonb WHERE id=%s",
                    (json.dumps(new_value), row[0]),
                )
                connection.execute(
                    "ALTER TABLE public.remote_publication_receipts ENABLE TRIGGER USER"
                )
                restore_values = (
                    "remote_update",
                    operation,
                    row[0],
                    "disclosure_projection",
                    old_value,
                )
            else:
                connection.execute(
                    """INSERT INTO remote_publication_receipts
                       (id, publication_attempt_id, publisher_id, remote_id, state,
                        safe_metadata_hash, disclosure_projection)
                       VALUES(%s,%s,'task4-fixture',%s,'accepted',%s,%s::jsonb)""",
                    (
                        inserted_id,
                        attempt["id"],
                        marker,
                        _sha256(marker.encode()),
                        json.dumps({"task4_mutation": marker}),
                    ),
                )
                restore_values = ("remote_append", operation, inserted_id)
        elif operation == "remote_publication_receipts":
            connection.execute(
                """INSERT INTO remote_publication_receipts
                   (id, publication_attempt_id, publisher_id, remote_id, state,
                    safe_metadata_hash, disclosure_projection)
                   VALUES(%s,%s,'task4-fixture',%s,'accepted',%s,%s::jsonb)""",
                (
                    inserted_id,
                    attempt["id"],
                    marker,
                    _sha256(marker.encode()),
                    json.dumps({"task4_mutation": marker}),
                ),
            )
            restore_values = ("remote_append", operation, inserted_id)
        elif operation == "job_checkpoints":
            sequence = connection.execute(
                "SELECT COALESCE(max(sequence_no),0)+1 FROM job_checkpoints WHERE job_id=%s",
                (job_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO job_checkpoints
                   (id, job_id, sequence_no, state, checkpoint_payload, trace_id)
                   VALUES(%s,%s,%s,'task4-mutation',%s::jsonb,%s)""",
                (inserted_id, job_id, sequence, json.dumps({"task4_mutation": marker}), marker),
            )
            restore_values = ("append", operation, inserted_id)
        elif operation == "audit_events":
            connection.execute(
                """INSERT INTO audit_events
                   (id, workspace_id, job_id, run_id, sequence_no, actor_kind,
                    action, resource_type, outcome, trace_id, details)
                   VALUES(%s,%s,%s,%s,1,'fixture','task4-mutation','job','test',%s,%s::jsonb)""",
                (
                    inserted_id,
                    workspace_id,
                    job_id,
                    marker,
                    marker,
                    json.dumps({"task4_mutation": marker}),
                ),
            )
            restore_values = ("append", operation, inserted_id)
        else:
            connection.execute(
                """INSERT INTO provenance_records
                   (id, workspace_id, job_id, origin_type, verification_status, lineage)
                   VALUES(%s,%s,%s,'task4-mutation','fixture',%s::jsonb)""",
                (inserted_id, workspace_id, job_id, json.dumps({"task4_mutation": marker})),
            )
            restore_values = ("append", operation, inserted_id)

    def restore():
        action, table_name = restore_values[:2]
        with psycopg.connect(database_url) as connection:
            if action in {"append", "remote_append"}:
                if action == "remote_append":
                    connection.execute(
                        "ALTER TABLE public.remote_publication_receipts DISABLE TRIGGER USER"
                    )
                connection.execute(
                    f"DELETE FROM {table_name} WHERE id=%s", (restore_values[2],)
                )
                if action == "remote_append":
                    connection.execute(
                        "ALTER TABLE public.remote_publication_receipts ENABLE TRIGGER USER"
                    )
            elif action in {"update", "remote_update"}:
                if action == "remote_update":
                    connection.execute(
                        "ALTER TABLE public.remote_publication_receipts DISABLE TRIGGER USER"
                    )
                connection.execute(
                    f"UPDATE {table_name} SET {restore_values[3]}=%s::jsonb WHERE id=%s",
                    (json.dumps(restore_values[4]), restore_values[2]),
                )
                if action == "remote_update":
                    connection.execute(
                        "ALTER TABLE public.remote_publication_receipts ENABLE TRIGGER USER"
                    )
            else:
                connection.execute(
                    f"INSERT INTO {table_name} SELECT * FROM jsonb_populate_record(NULL::public.{table_name}, %s::jsonb)",
                    (json.dumps(restore_values[3]),),
                )

    return restore


@pytest.mark.parametrize(
    "mutation",
    (
        "artifact-digest-mismatch",
        "lock-digest-mismatch",
        "launcher-hash-mismatch",
        "interpreter-hash-mismatch",
        "distribution-record-mismatch",
        "runtime-root-mismatch",
    ),
    ids=(
        "artifact-digest-mismatch",
        "lock-digest-mismatch",
        "launcher-hash-mismatch",
        "interpreter-hash-mismatch",
        "distribution-record-mismatch",
        "runtime-root-mismatch",
    ),
)
def test_candidate_image_launcher_denies_pin_mutations_before_registration(
    mutation, tmp_path
):
    candidate = _task8_rollback_image_or_skip()
    _assert_candidate_image(candidate)
    case_bundle = tmp_path / mutation
    case_bundle.mkdir()
    for name in (
        "launch-v4-publication-worker.py",
        "publication-worker-launch-lock.json",
        "publication-worker-launch-lock.sha256",
    ):
        shutil.copyfile(candidate["control_bundle"] / name, case_bundle / name)
    lock_path = case_bundle / "publication-worker-launch-lock.json"
    digest_path = case_bundle / "publication-worker-launch-lock.sha256"
    launcher_path = case_bundle / "launch-v4-publication-worker.py"
    lock = json.loads(lock_path.read_bytes())
    expected_diagnostics = {
        "artifact-digest-mismatch": "retained publication worker wheel hash mismatch",
        "lock-digest-mismatch": "launch-lock bytes do not match independent trust root",
        "launcher-hash-mismatch": (
            "publication worker source pin mismatch: scripts/launch-v4-publication-worker.py"
        ),
        "interpreter-hash-mismatch": "publication worker interpreter hash mismatch",
        "distribution-record-mismatch": "installed distribution record digest mismatch",
        "runtime-root-mismatch": "publication worker runtime root does not match the fixed root",
    }
    if mutation == "artifact-digest-mismatch":
        lock["pin"]["artifact_sha256"] = "0" * 64
    elif mutation == "lock-digest-mismatch":
        lock["launcher_sha256"] = "f" * 64
    elif mutation == "launcher-hash-mismatch":
        launcher_path.write_bytes(launcher_path.read_bytes() + b"\n# task8 mutation\n")
    elif mutation == "interpreter-hash-mismatch":
        lock["interpreter_sha256"] = "0" * 64
    elif mutation == "distribution-record-mismatch":
        lock["pin"]["distribution_record_sha256"] = "0" * 64
    elif mutation == "runtime-root-mismatch":
        lock["runtime_root"] = "/tmp/task8-runtime-root"
    else:
        raise AssertionError(f"unknown pin mutation: {mutation}")

    if mutation != "launcher-hash-mismatch":
        lock_bytes = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
        lock_path.write_bytes(lock_bytes)
        if mutation == "lock-digest-mismatch":
            # Keep the independent digest fixed to prove the altered lock cannot
            # authorize itself.
            digest_path.write_text(
                candidate["progress"]["launch_lock_sha256"] + "\n", encoding="ascii"
            )
        else:
            digest_path.write_text(_sha256(lock_bytes) + "\n", encoding="ascii")

    fixture = candidate["fixture"]
    operation_ids = fixture["operation_ids"]
    wrong_workspace = "00000000-0000-0000-0000-000000000096"
    assert wrong_workspace != fixture["workspace_id"]
    result = _load_worker_harness().invoke_launcher(
        image=candidate["image_id"],
        database_url=candidate["database_url"],
        control_bundle=case_bundle,
        argv=(
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "legacy-fixture",
            "--workspace-id",
            wrong_workspace,
            "--expired-hold-operation-id",
            operation_ids["expired_permit_receipt"],
            "--cancel-hold-operation-id",
            operation_ids["published_during_cancel"],
        ),
    )
    diagnostic = (result.stdout + result.stderr).casefold()
    assert result.returncode == 78, diagnostic
    assert expected_diagnostics[mutation] in diagnostic
    assert "both retained publication recovery holds failed verification" not in diagnostic
    assert "execve" not in diagnostic
    assert "salience.workflows.worker" not in diagnostic
    assert "salience.cycles.legacy_runtime" not in diagnostic


@pytest.mark.parametrize(
    "collection",
    (
        "remote-publication-receipts",
        "job-checkpoints",
        "audit-events",
        "provenance-records",
    ),
    ids=(
        "remote-publication-receipts",
        "job-checkpoints",
        "audit-events",
        "provenance-records",
    ),
)
@pytest.mark.asyncio
async def test_rollback_restart_launcher_denies_omitted_collection_mutation_before_registration(collection):
    candidate = _task8_rollback_image_or_skip()
    _assert_candidate_image(candidate)
    database_url = candidate["database_url"]
    initial = await _capture_candidate_external_state(candidate, database_url=database_url)
    mutated_reason = "published_during_cancel"
    restore = _mutate_omitted_liability_collection(
        candidate["database_url"],
        initial["liabilities"][mutated_reason],
        collection,
    )
    try:
        mutated = await _capture_candidate_external_state(
            candidate,
            database_url=database_url,
            validate_metadata_baseline=False,
        )
        snapshot_key = {
            "remote-publication-receipts": "remote_publication_receipts",
            "job-checkpoints": "job_checkpoints",
            "audit-events": "audit_events",
            "provenance-records": "provenance_records",
        }[collection]
        _assert_candidate_mutation_scope(initial, mutated, mutated_reason, snapshot_key)
        before_launcher = mutated
        result = _invoke_candidate_rollback_launcher(candidate)
        after_launcher = await _capture_candidate_external_state(
            candidate,
            database_url=database_url,
            validate_metadata_baseline=False,
        )
        _assert_candidate_launcher_denial_preserved_state(result, before_launcher, after_launcher)
    finally:
        restore()
    restored = await _capture_candidate_external_state(
        candidate, database_url=database_url
    )
    assert restored == initial


@pytest.mark.asyncio
async def test_rollback_restart_launcher_denies_legacy_null_v1_before_registration():
    candidate = _task8_rollback_image_or_skip(require_legacy_null=True)
    _assert_candidate_image(candidate)
    fixture = candidate["legacy_null_fixture"]
    assert set(fixture["operation_ids"]) == {
        "expired_permit_receipt",
        "published_during_cancel",
    }
    target = str(UUID(fixture["legacy_null_operation_id"]))
    assert target in {str(UUID(value)) for value in fixture["operation_ids"].values()}
    legacy_database_url = candidate["legacy_null_database_url"]
    assert legacy_database_url != candidate["database_url"]
    legacy_candidate = {
        **candidate,
        "fixture": fixture,
        "database_url": legacy_database_url,
        "database_target": "legacy-null-v1",
    }
    assert legacy_candidate["database_url"] == candidate["legacy_null_database_url"]
    state_before = await _capture_candidate_external_state(
        legacy_candidate, database_url=legacy_database_url
    )
    database_before = _capture_candidate_database_state(
        legacy_candidate, database_url=legacy_database_url
    )
    import psycopg

    with psycopg.connect(legacy_database_url) as connection:
        null_v1 = connection.execute(
            "SELECT liability_snapshot_version, liability_snapshot_v1, "
            "liability_fingerprint_v1 FROM v4_legacy_publication_recovery_holds "
            "WHERE operation_id=%s",
            (target,),
        ).fetchone()
    assert null_v1 == (None, None, None)
    launcher_targets = []

    def invoke_legacy_target(**kwargs):
        launcher_targets.append(kwargs["database_url"])
        return _load_worker_harness().invoke_launcher(**kwargs)

    result = _invoke_candidate_rollback_launcher(
        legacy_candidate, invoke=invoke_legacy_target
    )
    assert launcher_targets == [legacy_database_url]
    state_after = await _capture_candidate_external_state(
        legacy_candidate, database_url=legacy_database_url
    )
    _assert_candidate_launcher_denial_preserved_state(result, state_before, state_after)
    assert _capture_candidate_database_state(
        legacy_candidate, database_url=legacy_database_url
    ) == database_before


@pytest.mark.asyncio
async def test_rollback_restart_launcher_verifies_both_holds_before_exec():
    candidate = _task8_rollback_image_or_skip()
    _assert_candidate_image(candidate)
    before = await _capture_candidate_external_state(
        candidate, database_url=candidate["database_url"]
    )
    fixture = candidate["fixture"]
    operation_ids = fixture["operation_ids"]
    from uuid import uuid4

    wrong_workspace = str(uuid4())
    assert wrong_workspace != fixture["workspace_id"]
    argv = (
        "--mode",
        "rollback-restart",
        "--worker-kind",
        "legacy-fixture",
        "--workspace-id",
        wrong_workspace,
        "--expired-hold-operation-id",
        operation_ids["expired_permit_receipt"],
        "--cancel-hold-operation-id",
        operation_ids["published_during_cancel"],
    )
    result = _load_worker_harness().invoke_launcher(
        image=candidate["image_id"],
        database_url=candidate["database_url"],
        control_bundle=candidate["control_bundle"],
        argv=argv,
    )
    diagnostic = (result.stdout + result.stderr).casefold()
    assert result.returncode == 78, diagnostic
    assert "both retained publication recovery holds failed verification" in diagnostic
    assert "worker import" not in diagnostic
    assert "execve" not in diagnostic
    after = await _capture_candidate_external_state(
        candidate, database_url=candidate["database_url"]
    )
    assert after == before


@pytest.mark.asyncio
async def test_candidate_read_only_worker_creates_no_pyc_and_preserves_runtime_root():
    candidate = _task8_rollback_image_or_skip()
    _assert_candidate_image(candidate)
    database_url = candidate["database_url"]
    before = await _capture_candidate_external_state(candidate, database_url=database_url)
    harness = _load_worker_harness()
    result = _qualify_candidate_read_only_worker(
        candidate,
        image_manifest=harness.image_runtime_manifest,
        start_worker=harness.start_rollback_worker,
        inspect_worker=harness.inspect_worker,
        container_manifest=harness.container_runtime_manifest,
        stop_worker=harness.stop_worker,
    )
    assert result["readonly_rootfs"] is True
    after = await _capture_candidate_external_state(candidate, database_url=database_url)
    assert after == before


def test_pre_0043_database_is_rejected_by_hard_floor(tmp_path, monkeypatch):
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
        with pytest.raises(PublicationWorkerCompatibilityError, match="floor 0043"):
            require_publication_worker_compatibility(
                database,
                start_mode="current",
                launch_lock=lock,
                observed_launch_lock_sha256=_sha256(_lock_bytes(lock)),
            )
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


@pytest.mark.asyncio
async def test_legacy_fixture_main_denies_divergent_database_urls_before_connect_or_outbox(
    monkeypatch,
):
    from salience.cycles import legacy_runtime
    from salience.cycles import publication_recovery_compatibility as compatibility

    monkeypatch.setenv("DATABASE_URL", "postgresql://checked/verified_a")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://worker/fixture_b")
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("TEST_TEMPORAL_TARGET", "temporal.invalid:7233")
    monkeypatch.setenv("V4_LEGACY_FIXTURE_QUEUE", "legacy-fixture")
    monkeypatch.setenv("V4_FIXTURE_WORKSPACE", "95416086-6229-460e-8007-90a20873d1a2")
    monkeypatch.setattr(legacy_runtime, "require_fixture", lambda: None)

    gate_databases = []
    temporal_connects = []
    outbox_databases = []
    monkeypatch.setattr(
        compatibility,
        "require_publication_worker_compatibility_from_environment",
        lambda database_url: gate_databases.append(database_url),
    )

    async def fake_connect(*args, **kwargs):
        temporal_connects.append((args, kwargs))
        return object()

    def fake_outbox(database_url, **kwargs):
        outbox_databases.append(database_url)
        raise RuntimeError("CycleOutbox construction reached before mismatch denial")

    monkeypatch.setattr(legacy_runtime.Client, "connect", fake_connect)
    monkeypatch.setattr(legacy_runtime, "CycleOutbox", fake_outbox)

    with pytest.raises(PublicationWorkerCompatibilityError, match="DATABASE_URL"):
        await legacy_runtime.main()

    assert gate_databases == []
    assert temporal_connects == []
    assert outbox_databases == []


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


def test_authenticated_migration_graph_resolves_inside_site_packages(tmp_path, monkeypatch):
    from salience.cycles import publication_recovery_compatibility as compatibility

    lock, _, runtime_root, _ = _write_test_lock(tmp_path, monkeypatch)
    verified_source_files = compatibility._validate_lock_and_pin(
        lock, _sha256(_lock_bytes(lock))
    )
    assert set(MIGRATION_SUPPORT_PATHS).issubset(verified_source_files)
    assert all((runtime_root / _installed_path(path)).is_file() for path in MIGRATION_SUPPORT_PATHS)

    revisions, heads = compatibility._migration_graph(verified_source_files)

    assert "0043_complete_publication_recovery_liabilities" in revisions
    assert heads == {"0043_complete_publication_recovery_liabilities"}
    assert (runtime_root / "migrations/versions/0043_complete_publication_recovery_liabilities.py").is_file()


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


@pytest.mark.asyncio
async def test_floor_worker_executes_both_retained_reasons_and_second_verifier_passes(
    task8_monolithic_database, monkeypatch, tmp_path, request,
):
    candidate_root = ROOT / "artifacts/v4-next-p1/agents/p1-publication-recovery-0043"
    assert (candidate_root / "image-attestation.json").is_file()
    assert (candidate_root / "worker-image-id.txt").is_file()
    assert (candidate_root / "control-bundle").is_dir()
    qualification_worker = ROOT / "tests/fixtures/v4_publication_floor_worker.py"
    assert qualification_worker.is_file(), "actual pinned-image fixture harness is required"

    import importlib.util
    import shutil
    import subprocess
    from uuid import uuid4

    import psycopg
    from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
    from temporalio.api.enums.v1 import PendingActivityState

    from test_v4_legacy_publication import (
        _allow,
        _exception_chain,
        _hard_exit_publication_worker,
        _publication_fixture,
        _recovery_projection_counts,
        _wait_for_publication_permit_expiry,
        _wait_for_activity_retry_ready,
    )
    from test_v4_publication_recovery_holds import (
        _assert_named_liabilities_unchanged,
        _create_database,
        _drop_database,
        _hold_values,
        _INSERT_SQL,
        _liability_snapshot,
        _migrate,
        _recovery_hold_count,
        _recovery_hold_snapshot,
    )
    from psycopg.rows import dict_row
    from salience.cycles.outbox import CycleOutbox, enqueue_cycle_message
    from salience.cycles.publication_recovery import (
        record_publication_recovery_hold,
        verify_publication_recovery_rollback,
    )
    from salience.cycles.runtime import TemporalCycleTransport

    database_admin_url = os.environ["TEST_DATABASE_URL"]
    persist_fixtures = os.environ.get("P1_TASK8_PERSIST_FIXTURES") == "1"
    database = task8_monolithic_database
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    monkeypatch.setenv("TEST_DATABASE_URL", database)

    spec = importlib.util.spec_from_file_location(
        "v4_publication_floor_worker", qualification_worker
    )
    assert spec is not None and spec.loader is not None
    worker_harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker_harness)

    evidence_root = ROOT / "artifacts/v4-next-p1/agents/p1-publication-recovery-0043"
    tracked_lock_path = ROOT / "deployment/v4/publication-worker-launch-lock.json"
    tracked_lock_bytes = tracked_lock_path.read_bytes()
    tracked_lock = json.loads(tracked_lock_bytes)
    application_commit = tracked_lock["pin"]["application_commit"]
    candidate_suffix = application_commit[:7]
    candidate_root = evidence_root
    control_bundle = candidate_root / "control-bundle"
    attestation = json.loads(
        (candidate_root / "image-attestation.json").read_text(encoding="utf-8")
    )
    image_ref = attestation["image_ref"]
    expected_image_id = attestation["image_id"]
    trusted_digest = hashlib.sha256(tracked_lock_bytes).hexdigest()
    assert (control_bundle / "publication-worker-launch-lock.json").read_bytes() == tracked_lock_bytes
    assert (control_bundle / "publication-worker-launch-lock.sha256").read_text(
        encoding="ascii"
    ).strip() == trusted_digest
    assert hashlib.sha256(
        (control_bundle / "launch-v4-publication-worker.py").read_bytes()
    ).hexdigest() == tracked_lock["launcher_sha256"]
    assert attestation["application_commit"] == application_commit
    assert image_ref == f"salience-p1-floor-{candidate_suffix}-worker:latest"
    assert tracked_lock["pin"]["artifact_sha256"] == attestation["wheel_sha256"]
    assert tracked_lock["pin"]["distribution_record_sha256"] == attestation["record_sha256"]
    assert attestation["record_ok"] is True
    assert attestation["pyc_files"] == []
    assert attestation["source_tree_loaded"] is False
    runtime_manifest = worker_harness.image_runtime_manifest(
        expected_image_id, attestation["runtime_root"]
    )
    assert runtime_manifest["pyc_files"] == []
    assert _canonical_json_hash(runtime_manifest) == attestation["runtime_manifest_sha256"]

    def canonical_bytes(value):
        return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()

    def canonical_hash(value):
        return hashlib.sha256(canonical_bytes(value)).hexdigest()

    def save_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def read_cycle_runtime_database_state(cycle_id):
        from psycopg.rows import dict_row

        with psycopg.connect(database, row_factory=dict_row) as connection:
            cycle = connection.execute(
                "SELECT id::text AS id,state,closed_at::text AS closed_at "
                "FROM v4_cycles WHERE id=%s",
                (cycle_id,),
            ).fetchone()
            outbox_rows = connection.execute(
                """SELECT message.id::text AS id,message.kind,message.sequence,message.state,
                          message.attempts,receipt.state AS receipt_state
                   FROM v4_cycle_outbox message
                   LEFT JOIN v4_cycle_inbox receipt ON receipt.message_id=message.id
                   WHERE message.cycle_id=%s ORDER BY message.sequence""",
                (cycle_id,),
            ).fetchall()
            cycle_events = connection.execute(
                """SELECT to_jsonb(event) AS event FROM v4_cycle_events event
                   WHERE event.cycle_id=%s ORDER BY event.id""",
                (cycle_id,),
            ).fetchall()
            runtime_holds = connection.execute(
                "SELECT to_jsonb(hold) AS hold FROM v4_runtime_holds hold "
                "WHERE hold.cycle_id=%s",
                (cycle_id,),
            ).fetchall()
        assert cycle is not None
        return {
            "cycle": dict(cycle),
            "outbox_rows": [dict(row) for row in outbox_rows],
            "cycle_events": [row["event"] for row in cycle_events],
            "runtime_holds": [row["hold"] for row in runtime_holds],
        }

    def safe_text(value):
        return value.replace(database, "<redacted-database-url>")

    def save_candidate_lock(name, candidate):
        raw = canonical_bytes(candidate)
        path = tmp_path / f"{name}-candidate-lock.json"
        path.write_bytes(raw)
        digest_path = tmp_path / f"{name}-candidate-lock.sha256"
        digest_path.write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii")
        return path, digest_path

    base_evidence = {
        "schema_version": "salience.publication-floor-qualification.v1",
        "application_commit": tracked_lock["pin"]["application_commit"],
        "eligible_anchor": ANCHOR,
        "application_ancestry": None,
        "wheel_build_command": attestation["wheel_build_command"],
        "compose_build_command": attestation["compose_build_command"],
        "image_ref": image_ref,
        "image_id": expected_image_id,
        "wheel_sha256": attestation["wheel_sha256"],
        "wheel_path": attestation["wheel_path"],
        "record_path": attestation["record_path"],
        "record_sha256": attestation["record_sha256"],
        "record_entry_count": attestation["record_entry_count"],
        "record_ok": attestation["record_ok"],
        "pyc_files": attestation["pyc_files"],
        "source_tree_loaded": attestation["source_tree_loaded"],
        "runtime_root": attestation["runtime_root"],
        "interpreter": tracked_lock["interpreter_path"],
        "interpreter_sha256": tracked_lock["interpreter_sha256"],
        "source_manifest_sha256": tracked_lock["pin"]["source_manifest_sha256"],
        "source_files": tracked_lock["pin"]["source_files"],
        "runtime_imports": tracked_lock["pin"]["runtime_imports"],
        "launcher_sha256": tracked_lock["launcher_sha256"],
        "launch_lock_sha256": trusted_digest,
        "rollback_argv": None,
        "workspace_id": None,
        "operation_ids": {},
        "hold_ids": {},
        "first_verifier": None,
        "second_verifier": None,
        "third_verifier": None,
        "liability_snapshot_hashes": {},
        "hold_row_hashes": {},
        "projection_counts_before": {},
        "projection_counts_after": {},
        "worker_runs": [],
        "denials": {},
        "fixture_terminal_boundaries": {},
        "effects_enabled": False,
        "production_effects_enabled": False,
        "fixture_publication_budget": 0,
        "known_running_image_workers_before_restart": None,
    }
    progress_path = candidate_root / "worker-qualification-progress.json"
    final_evidence_path = candidate_root / "worker-qualification.json"
    progress_path.unlink(missing_ok=True)
    if not persist_fixtures:
        request.addfinalizer(lambda: progress_path.unlink(missing_ok=True))
    save_json(progress_path, base_evidence)

    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ANCHOR, tracked_lock["pin"]["application_commit"]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert ancestry.returncode == 0, safe_text(ancestry.stderr)
    base_evidence["application_ancestry"] = {
        "anchor": ANCHOR,
        "candidate": tracked_lock["pin"]["application_commit"],
        "is_ancestor": True,
        "command": f"git merge-base --is-ancestor {ANCHOR} {tracked_lock['pin']['application_commit']}",
        "exit_code": ancestry.returncode,
    }

    # Create the authentic cancellation hold, waiting for Temporal to persist the
    # cancellation signal before the crash-after-commit recorder runs.
    cancelled_fixture = await _publication_fixture(database)
    cancelled = cancelled_fixture["bridge"].submit_publication(cancelled_fixture["command"])
    cancelled_operation_id = UUID(cancelled["operation_id"])
    workspace_id = cancelled_fixture["workspace_id"]
    _allow(
        database,
        workspace_id,
        cancelled_fixture["actor_id"],
        ("legacy:publication:reconcile:account:" + cancelled_fixture["account"].id,),
    )
    terminal_boundary_snapshots = {}

    async def close_after_processing_read():
        with psycopg.connect(database) as connection:
            updated = connection.execute(
                """UPDATE v4_legacy_publication_fixture_progress
                   SET state='published', polls=2, updated_at=clock_timestamp()
                   WHERE operation_id=%s AND state IN ('accepted','processing')
                   RETURNING state""",
                (str(cancelled_operation_id),),
            ).fetchone()
        assert updated == ("published",)
        cancelled_fixture["bridge"].close(
            cancelled["cycle_id"],
            disposition="cancelled",
            reason="Preserve accepted publication liability while reading its final remote state",
        )
        client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
        outbox = CycleOutbox(
            database, workspace_id=workspace_id, delivery_lane="legacy"
        )
        assert await outbox.dispatch_one(
            TemporalCycleTransport(client, task_queue=cancelled_fixture["queue"])
        )
        handle = client.get_workflow_handle(
            "salience-v4-legacy-operation:" + str(cancelled_operation_id)
        )
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(
                    event.HasField("workflow_execution_signaled_event_attributes")
                    and event.workflow_execution_signaled_event_attributes.signal_name
                    == "request_cancellation"
                    for event in history.events
                ):
                    break
                description = await handle.describe()
                assert description.status == WorkflowExecutionStatus.RUNNING
                await asyncio.sleep(0.05)
        local_cycle_handle = client.get_workflow_handle(
            "salience-v4-local:" + str(cancelled["cycle_id"])
        )
        local_cycle_result = await asyncio.wait_for(local_cycle_handle.result(), timeout=10)
        assert local_cycle_result == {
            "cycle_id": str(cancelled["cycle_id"]),
            "state": "recorded",
        }
        local_cycle_description = await local_cycle_handle.describe()
        assert local_cycle_description.status == WorkflowExecutionStatus.COMPLETED
        local_cycle_history = await local_cycle_handle.fetch_history()
        cancelled_cycle_state = read_cycle_runtime_database_state(
            cancelled["cycle_id"]
        )
        assert cancelled_cycle_state["cycle"]["state"] == "closed"
        assert cancelled_cycle_state["cycle"]["closed_at"] is not None
        assert [
            (row["kind"], row["state"], row["attempts"], row["receipt_state"])
            for row in cancelled_cycle_state["outbox_rows"]
        ] == [
            ("start", "delivered", 1, "recorded"),
            ("close", "delivered", 1, "recorded"),
        ]
        assert all(row["state"] == "delivered" for row in cancelled_cycle_state["outbox_rows"])
        assert cancelled_cycle_state["runtime_holds"] == []
        base_evidence["fixture_terminal_boundaries"]["published_during_cancel"] = {
            "cycle_id": str(cancelled["cycle_id"]),
            "local_workflow_id": "salience-v4-local:" + str(cancelled["cycle_id"]),
            "local_workflow_status": local_cycle_description.status.name,
            "local_workflow_run_id": local_cycle_description.run_id,
            "local_workflow_history_last_event_id": max(
                event.event_id for event in local_cycle_history.events
            ),
            "local_workflow_history_sha256": _temporal_history_sha256(
                local_cycle_history.events
            ),
            "database_state_sha256": canonical_hash(cancelled_cycle_state),
            "cycle_events_sha256": canonical_hash(cancelled_cycle_state["cycle_events"]),
            "outbox_rows": cancelled_cycle_state["outbox_rows"],
            "outbox_row_count": len(cancelled_cycle_state["outbox_rows"]),
            "pending_outbox_rows": 0,
        }
        save_json(progress_path, base_evidence)

    async def capture_cancel_pre_hold_snapshot():
        pre_hold_snapshot = _liability_snapshot(database, cancelled_operation_id)
        status_events = pre_hold_snapshot["publication_status_events"]
        assert len(status_events) == 2, status_events
        processing_events = [
            event
            for event in status_events
            if event["state"] == "processing" and event["source"] == "webhook"
        ]
        assert len(processing_events) == 1, status_events
        assert processing_events[0]["publication_attempt_id"] == (
            pre_hold_snapshot["publication_attempt"]["id"]
        )
        assert str(pre_hold_snapshot["cycle"]["operation_id"]) == str(
            cancelled_operation_id
        )
        cycle_state = read_cycle_runtime_database_state(cancelled["cycle_id"])
        boundary = base_evidence["fixture_terminal_boundaries"][
            "published_during_cancel"
        ]
        assert canonical_hash(cycle_state) == boundary["database_state_sha256"]
        assert canonical_hash(cycle_state["cycle_events"]) == boundary[
            "cycle_events_sha256"
        ]
        terminal_boundary_snapshots["published_during_cancel"] = pre_hold_snapshot
        boundary["pre_hold_status_event_id"] = processing_events[0]["id"]
        boundary["pre_hold_status_event_publication_attempt_id"] = processing_events[0][
            "publication_attempt_id"
        ]
        boundary["liability_snapshot_sha256"] = canonical_hash(pre_hold_snapshot)
        save_json(progress_path, base_evidence)

    cancel_precommit_path = tmp_path / "cancel-hold-precommit-liabilities.json"
    cancel_worker_diagnostic_path = tmp_path / "cancel-hold-worker-diagnostics.jsonl"
    try:
        cancel_exit = await _hard_exit_publication_worker(
            cancelled_fixture,
            tmp_path,
            after_acceptance=False,
            after_hold_commit=True,
            precommit_snapshot_path=cancel_precommit_path,
            operation_id=str(cancelled_operation_id),
            cancel_after_processing=True,
            cancel_ready_callback=close_after_processing_read,
            cancel_hold_ready_callback=capture_cancel_pre_hold_snapshot,
            diagnostic_path=cancel_worker_diagnostic_path,
        )
    finally:
        if cancel_worker_diagnostic_path.is_file():
            diagnostic_archive = candidate_root / (
                f"task8-cancel-worker-diagnostics-{candidate_suffix}-{uuid4().hex[:8]}.jsonl"
            )
            diagnostic_archive.write_text(
                safe_text(cancel_worker_diagnostic_path.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            base_evidence["fixture_terminal_boundaries"][
                "published_during_cancel"
            ]["setup_worker_log_path"] = str(
                diagnostic_archive.relative_to(candidate_root)
            )
            base_evidence["fixture_terminal_boundaries"][
                "published_during_cancel"
            ]["setup_worker_log_sha256"] = hashlib.sha256(
                diagnostic_archive.read_bytes()
            ).hexdigest()
            save_json(progress_path, base_evidence)
    assert cancel_exit == 74, (
        f"cancel crash-after-commit worker exited {cancel_exit}; "
        f"diagnostics={safe_text(cancel_precommit_path.read_text()) if cancel_precommit_path.exists() else 'missing'}"
    )
    assert cancel_precommit_path.is_file()
    cancel_before = json.loads(cancel_precommit_path.read_text(encoding="utf-8"))
    cancel_after_hold = _liability_snapshot(database, cancelled_operation_id)
    _assert_named_liabilities_unchanged(
        terminal_boundary_snapshots["published_during_cancel"],
        cancel_before,
    )
    _assert_named_liabilities_unchanged(cancel_before, cancel_after_hold)
    cancelled_hold_row = _recovery_hold_snapshot(database, cancelled_operation_id)
    assert cancelled_hold_row is not None
    assert cancelled_hold_row["reason"] == "published_during_cancel"
    assert _recovery_hold_count(database, cancelled_operation_id) == 1

    # The expired-receipt hold uses a distinct queue and operation in the same
    # workspace, while sharing the exact approved ready package.
    import test_creative_release_gate_migration as release_gate

    original_ready_package = release_gate._approved_ready_package

    async def reuse_cancelled_ready_package(*, publication_proofs=True):
        return cancelled_fixture["ready"]

    monkeypatch.setattr(release_gate, "_approved_ready_package", reuse_cancelled_ready_package)
    try:
        expired_fixture = await _publication_fixture(database)
    finally:
        monkeypatch.setattr(release_gate, "_approved_ready_package", original_ready_package)
    assert expired_fixture["workspace_id"] == workspace_id
    assert expired_fixture["queue"] != cancelled_fixture["queue"]
    _allow(
        database,
        workspace_id,
        expired_fixture["actor_id"],
        ("legacy:publication:reconcile:account:" + expired_fixture["account"].id,),
    )
    expired = expired_fixture["bridge"].submit_publication(expired_fixture["command"])
    expired_operation_id = UUID(expired["operation_id"])
    expired_local_cycle_id = str(expired["cycle_id"])
    expired_local_workflow_id = "salience-v4-local:" + expired_local_cycle_id
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    expired_local_handle = client.get_workflow_handle(expired_local_workflow_id)
    expired_quiescence = {}

    async def wait_for_publication_submit_task_state(submit_context):
        history = None
        expected_workflow_id = (
            "salience-v4-legacy-operation:" + str(expired_operation_id)
        )
        history_handle = client.get_workflow_handle(
            expected_workflow_id,
            run_id=submit_context["workflow_run_id"],
        )
        try:
            async with asyncio.timeout(10):
                while True:
                    description = await history_handle.describe()
                    if (
                        description.id != expected_workflow_id
                        or description.run_id != submit_context["workflow_run_id"]
                    ):
                        raise AssertionError("submit activity workflow/run changed at the fixture barrier")
                    pending = [
                        item
                        for item in description.raw_description.pending_activities
                        if item.activity_id == submit_context["activity_id"]
                        and item.activity_type.name
                        == "salience.publication.submit_or_reconcile"
                    ]
                    if (
                        len(pending) == 1
                        and pending[0].attempt == 1
                        and pending[0].state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
                    ):
                        history = await history_handle.fetch_history()
                        scheduled_ids = [
                            event.event_id
                            for event in history.events
                            if event.HasField("activity_task_scheduled_event_attributes")
                            and event.activity_task_scheduled_event_attributes.activity_id
                            == submit_context["activity_id"]
                            and event.activity_task_scheduled_event_attributes.activity_type.name
                            == "salience.publication.submit_or_reconcile"
                        ]
                        started_ids = [
                            event.event_id
                            for event in history.events
                            if event.HasField("activity_task_started_event_attributes")
                            and event.activity_task_started_event_attributes.scheduled_event_id
                            in scheduled_ids
                        ]
                        assert len(scheduled_ids) == 1
                        return {
                            "activity_id": pending[0].activity_id,
                            "activity_type": pending[0].activity_type.name,
                            "attempt": pending[0].attempt,
                            "pending_state": PendingActivityState.Name(pending[0].state),
                            "scheduled_event_ids": scheduled_ids,
                            "started_event_ids": started_ids,
                        }
        except TimeoutError as error:
            observed = [] if history is None else [event.event_id for event in history.events]
            raise TimeoutError(
                "submit-entry activity did not reach exact started state: "
                f"workflow={expected_workflow_id} "
                f"run={submit_context['workflow_run_id']} "
                f"activity={submit_context['activity_id']} observed_event_ids={observed}"
            ) from error

    async def close_expired_runtime_before_acceptance(submit_context):
        assert submit_context["workflow_id"] == (
            "salience-v4-legacy-operation:" + str(expired_operation_id)
        )
        expired_local_before = await expired_local_handle.describe()
        assert expired_local_before.status == WorkflowExecutionStatus.RUNNING
        expired_local_history_before = await expired_local_handle.fetch_history()
        assert not any(
            event.HasField("activity_task_scheduled_event_attributes")
            and event.activity_task_scheduled_event_attributes.activity_type.name
            == "salience.v4.fixture_runtime_hold"
            for event in expired_local_history_before.events
        )
        expired_cycle_state_before = read_cycle_runtime_database_state(
            expired_local_cycle_id
        )
        assert expired_cycle_state_before["cycle"]["state"] == "runnable"
        assert expired_cycle_state_before["cycle"]["closed_at"] is None
        assert expired_cycle_state_before["runtime_holds"] == []
        assert len(expired_cycle_state_before["outbox_rows"]) == 1
        assert expired_cycle_state_before["outbox_rows"][0]["kind"] == "start"
        assert expired_cycle_state_before["outbox_rows"][0]["state"] == "delivered"
        assert expired_cycle_state_before["outbox_rows"][0]["receipt_state"] == "recorded"
        submit_state_before_close = await wait_for_publication_submit_task_state(
            submit_context
        )

        close_payload = {
            "disposition": "cancelled",
            "operation_id": str(expired_operation_id),
        }
        with psycopg.connect(database, row_factory=dict_row) as connection:
            start_binding = connection.execute(
                """SELECT message.workspace_id,message.subject_id,message.goal_id,
                          message.intent_id,message.cycle_id,message.traceparent,
                          cycle.operation_id,cycle.state
                   FROM v4_cycle_outbox message
                   JOIN v4_cycles cycle ON cycle.id=message.cycle_id
                   WHERE message.cycle_id=%s AND message.kind='start'
                     AND message.sequence=1""",
                (expired_local_cycle_id,),
            ).fetchone()
            assert start_binding is not None
            assert str(start_binding["operation_id"]) == str(expired_operation_id)
            assert start_binding["state"] == "runnable"
            enqueue_cycle_message(
                connection,
                workspace_id=start_binding["workspace_id"],
                subject_id=start_binding["subject_id"],
                goal_id=start_binding["goal_id"],
                intent_id=start_binding["intent_id"],
                cycle_id=start_binding["cycle_id"],
                kind="close",
                payload=close_payload,
                traceparent=start_binding["traceparent"],
            )
            close_binding = connection.execute(
                """SELECT id::text AS id,workspace_id,subject_id,goal_id,intent_id,
                          cycle_id::text AS cycle_id,sequence,kind,payload,traceparent
                   FROM v4_cycle_outbox
                   WHERE cycle_id=%s AND kind='close' AND sequence=2""",
                (expired_local_cycle_id,),
            ).fetchone()
            assert close_binding is not None
            assert close_binding["payload"] == close_payload
            assert close_binding["sequence"] == 2
            pending_workspace_rows = connection.execute(
                """SELECT id::text AS id,cycle_id::text AS cycle_id,kind,state
                   FROM v4_cycle_outbox
                   WHERE workspace_id=%s AND state!='delivered'""",
                (start_binding["workspace_id"],),
            ).fetchall()
        assert len(pending_workspace_rows) == 1
        assert pending_workspace_rows[0]["id"] == close_binding["id"]
        assert pending_workspace_rows[0]["cycle_id"] == expired_local_cycle_id

        close_outbox = CycleOutbox(
            database,
            workspace_id=workspace_id,
            delivery_lane="legacy",
        )
        dispatched = await close_outbox.dispatch_one(
            TemporalCycleTransport(client, task_queue=expired_fixture["queue"])
        )
        assert dispatched is True
        expired_local_result = await asyncio.wait_for(
            expired_local_handle.result(), timeout=10
        )
        expired_local_after = await expired_local_handle.describe()
        assert expired_local_result == {
            "cycle_id": expired_local_cycle_id,
            "state": "recorded",
        }
        assert expired_local_after.status == WorkflowExecutionStatus.COMPLETED
        expired_local_history_after = await expired_local_handle.fetch_history()
        assert expired_local_history_after.events[-1].HasField(
            "workflow_execution_completed_event_attributes"
        )
        original_local_build_ids = {
            event.workflow_task_completed_event_attributes.worker_version.build_id
            for event in expired_local_history_before.events
            if event.HasField("workflow_task_completed_event_attributes")
            and event.workflow_task_completed_event_attributes.worker_version.build_id
        }
        assert len(original_local_build_ids) == 1
        close_worker_build_ids = {
            event.workflow_task_completed_event_attributes.worker_version.build_id
            for event in expired_local_history_after.events
            if event.HasField("workflow_task_completed_event_attributes")
            and event.event_id
            > max(item.event_id for item in expired_local_history_before.events)
            and event.workflow_task_completed_event_attributes.worker_version.build_id
        }
        assert close_worker_build_ids == original_local_build_ids

        close_signal_events = [
            event
            for event in expired_local_history_after.events
            if event.HasField("workflow_execution_signaled_event_attributes")
            and event.workflow_execution_signaled_event_attributes.signal_name == "deliver"
            and json.loads(
                event.workflow_execution_signaled_event_attributes.input.payloads[0].data
            ).get("message_id")
            == close_binding["id"]
            and json.loads(
                event.workflow_execution_signaled_event_attributes.input.payloads[0].data
            ).get("kind")
            == "close"
        ]
        assert len(close_signal_events) == 1
        close_activity_scheduled_events = []
        for event in expired_local_history_after.events:
            if not event.HasField("activity_task_scheduled_event_attributes"):
                continue
            attributes = event.activity_task_scheduled_event_attributes
            if attributes.activity_type.name != "salience.v4.fixture_consume":
                continue
            payloads = attributes.input.payloads
            if payloads and json.loads(payloads[0].data).get("message_id") == close_binding["id"]:
                close_activity_scheduled_events.append(event)
        assert len(close_activity_scheduled_events) == 1
        close_activity_scheduled = close_activity_scheduled_events[0]
        close_activity_completed_events = [
            event
            for event in expired_local_history_after.events
            if event.HasField("activity_task_completed_event_attributes")
            and event.activity_task_completed_event_attributes.scheduled_event_id
            == close_activity_scheduled.event_id
        ]
        assert len(close_activity_completed_events) == 1
        workflow_completed_event = expired_local_history_after.events[-1]
        assert (
            close_signal_events[0].event_id
            < close_activity_scheduled.event_id
            < close_activity_completed_events[0].event_id
            < workflow_completed_event.event_id
        )

        with psycopg.connect(database, row_factory=dict_row) as connection:
            close_receipt = connection.execute(
                """SELECT message.id::text AS id,message.kind,message.sequence,
                          message.state,message.attempts,message.payload,
                          receipt.state AS receipt_state
                   FROM v4_cycle_outbox message
                   JOIN v4_cycle_inbox receipt ON receipt.message_id=message.id
                   WHERE message.id=%s AND message.cycle_id=%s""",
                (close_binding["id"], expired_local_cycle_id),
            ).fetchone()
            accepted_receipt_before_release = connection.execute(
                """SELECT count(*) AS row_count FROM v4_legacy_publication_fixture_receipts
                   WHERE operation_id=%s""",
                (expired_operation_id,),
            ).fetchone()["row_count"]
        assert close_receipt is not None
        assert close_receipt["id"] == close_binding["id"]
        assert close_receipt["kind"] == "close"
        assert close_receipt["sequence"] == 2
        assert close_receipt["state"] == "delivered"
        assert close_receipt["attempts"] == 1
        assert close_receipt["payload"] == close_payload
        assert close_receipt["receipt_state"] == "recorded"
        assert accepted_receipt_before_release == 0, (
            "close receipt and local Temporal completion must precede publication acceptance"
        )

        expired_cycle_state_after = read_cycle_runtime_database_state(
            expired_local_cycle_id
        )
        assert expired_cycle_state_after["cycle"] == expired_cycle_state_before["cycle"]
        assert expired_cycle_state_after["runtime_holds"] == []
        assert [
            (row["kind"], row["state"], row["attempts"], row["receipt_state"])
            for row in expired_cycle_state_after["outbox_rows"]
        ] == [
            ("start", "delivered", 1, "recorded"),
            ("close", "delivered", 1, "recorded"),
        ]
        prior_event_ids = {
            event["id"] for event in expired_cycle_state_before["cycle_events"]
        }
        close_events = [
            event
            for event in expired_cycle_state_after["cycle_events"]
            if event["id"] not in prior_event_ids
        ]
        assert len(close_events) == 1
        assert close_events[0]["kind"] == "fixture_consumed"
        assert close_events[0]["payload"]["message_id"] == close_binding["id"]
        assert close_events[0]["payload"]["state"] == "recorded"
        assert all(
            row["state"] == "delivered" and row["receipt_state"] == "recorded"
            for row in expired_cycle_state_after["outbox_rows"]
        ), "expired fixture has pending runtime work that could append a later cycle event"
        submit_state_during_close = await wait_for_publication_submit_task_state(
            submit_context
        )
        assert submit_state_during_close == submit_state_before_close
        expired_quiescence.update(
            {
                "submit_context": submit_context,
                "local_workflow_before": expired_local_before,
                "local_history_before": expired_local_history_before,
                "cycle_state_before": expired_cycle_state_before,
                "local_result": expired_local_result,
                "local_workflow_after": expired_local_after,
                "local_history_after_close": expired_local_history_after,
                "cycle_state_after_close": expired_cycle_state_after,
                "close_payload": close_payload,
                "close_binding": dict(close_binding),
                "close_receipt": dict(close_receipt),
                "close_signal_event": close_signal_events[0],
                "close_activity_scheduled_event": close_activity_scheduled,
                "close_activity_completed_event": close_activity_completed_events[0],
                "workflow_completed_event": workflow_completed_event,
                "close_worker_build_ids": close_worker_build_ids,
                "publication_submit_state_before": submit_state_before_close,
                "publication_submit_state_during": submit_state_during_close,
                "accepted_receipt_absent_before_release": True,
            }
        )

    expired_worker_diagnostic_path = (
        tmp_path / "expired-permit-worker-diagnostics.jsonl"
    )
    expired_worker_exit = await _hard_exit_publication_worker(
        expired_fixture,
        tmp_path,
        after_acceptance=True,
        permit_ttl_seconds=5,
        operation_id=str(expired_operation_id),
        diagnostic_path=expired_worker_diagnostic_path,
        submit_ready_callback=close_expired_runtime_before_acceptance,
    )
    assert expired_worker_diagnostic_path.is_file()
    expired_worker_events = [
        json.loads(line)
        for line in expired_worker_diagnostic_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    worker_event_names = [event.get("event") for event in expired_worker_events]
    assert worker_event_names.index("submit_entry_ready") < worker_event_names.index(
        "submit_entry_released"
    ) < worker_event_names.index("submit_started") < worker_event_names.index(
        "submit_returned"
    )
    submit_returned_event = next(
        event for event in expired_worker_events if event.get("event") == "submit_returned"
    )
    assert submit_returned_event["state"] == "accepted"
    worker_process_exit_event = next(
        event for event in expired_worker_events if event.get("event") == "worker_process_exit"
    )
    assert worker_process_exit_event["return_code"] == 73
    assert expired_worker_exit == 73
    await _wait_for_publication_permit_expiry(database, str(expired_operation_id))

    expired_local_history_final = await expired_local_handle.fetch_history()
    expired_local_after_worker_exit = await expired_local_handle.describe()
    assert expired_local_after_worker_exit.status == WorkflowExecutionStatus.COMPLETED
    assert _temporal_history_sha256(expired_local_history_final.events) == (
        _temporal_history_sha256(expired_quiescence["local_history_after_close"].events)
    )
    assert max(event.event_id for event in expired_local_history_final.events) == max(
        event.event_id for event in expired_quiescence["local_history_after_close"].events
    )
    submit_context = expired_quiescence["submit_context"]
    publication_handle_after = client.get_workflow_handle(
        submit_context["workflow_id"], run_id=submit_context["workflow_run_id"]
    )
    publication_description_after = await publication_handle_after.describe()
    publication_history_after = await publication_handle_after.fetch_history()
    publication_submit_schedule_ids_after = [
        event.event_id
        for event in publication_history_after.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and event.activity_task_scheduled_event_attributes.activity_id
        == submit_context["activity_id"]
        and event.activity_task_scheduled_event_attributes.activity_type.name
        == "salience.publication.submit_or_reconcile"
    ]
    publication_submit_started_ids_after = [
        event.event_id
        for event in publication_history_after.events
        if event.HasField("activity_task_started_event_attributes")
        and event.activity_task_started_event_attributes.scheduled_event_id
        in publication_submit_schedule_ids_after
    ]
    assert publication_submit_schedule_ids_after == expired_quiescence[
        "publication_submit_state_during"
    ]["scheduled_event_ids"]
    assert publication_submit_started_ids_after == expired_quiescence[
        "publication_submit_state_during"
    ]["started_event_ids"]
    pending_submit_activities_after = [
        item
        for item in publication_description_after.raw_description.pending_activities
        if item.activity_id == submit_context["activity_id"]
        and item.activity_type.name == "salience.publication.submit_or_reconcile"
    ]
    assert not any(
        item.attempt > 1
        and item.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
        for item in pending_submit_activities_after
    ), "a publication retry attempt started after the worker's accepted result was lost"
    publication_submit_state_after = {
        "scheduled_event_ids": publication_submit_schedule_ids_after,
        "started_event_ids": publication_submit_started_ids_after,
        "pending": [
            {
                "activity_id": item.activity_id,
                "activity_type": item.activity_type.name,
                "attempt": item.attempt,
                "state": PendingActivityState.Name(item.state),
            }
            for item in pending_submit_activities_after
        ],
    }
    expired_cycle_state_after_worker_exit = read_cycle_runtime_database_state(
        expired_local_cycle_id
    )
    assert expired_cycle_state_after_worker_exit == expired_quiescence[
        "cycle_state_after_close"
    ], "runtime actor changed cycle evidence after the quiescent close boundary"
    with psycopg.connect(database, row_factory=dict_row) as connection:
        accepted_receipt = connection.execute(
            """SELECT receipt.receipt->>'state' AS receipt_state,
                      progress.state AS progress_state,
                      receipt.idempotency_key,receipt.remote_id
               FROM v4_legacy_publication_fixture_receipts receipt
               JOIN v4_legacy_publication_fixture_progress progress USING(operation_id)
               WHERE receipt.operation_id=%s""",
            (expired_operation_id,),
        ).fetchone()
    assert accepted_receipt is not None
    assert accepted_receipt["receipt_state"] == "accepted"
    assert accepted_receipt["progress_state"] == "accepted"
    assert accepted_receipt["remote_id"] == (
        "fixture-v4-publication:" + str(expired_operation_id)
    )
    expired_terminal_boundary = _liability_snapshot(database, expired_operation_id)
    terminal_boundary_snapshots["expired_permit_receipt"] = expired_terminal_boundary
    diagnostic_archive = candidate_root / (
        f"task8-expired-worker-diagnostics-{candidate_suffix}-{uuid4().hex[:8]}.jsonl"
    )
    diagnostic_archive.write_text(
        safe_text(expired_worker_diagnostic_path.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    base_evidence["expired_worker_diagnostics"] = {
        "exit_code": expired_worker_exit,
        "path": str(diagnostic_archive.relative_to(candidate_root)),
        "sha256": hashlib.sha256(diagnostic_archive.read_bytes()).hexdigest(),
        "events": expired_worker_events,
    }
    expired_cycle_state_after = expired_quiescence["cycle_state_after_close"]
    expired_local_history_after = expired_quiescence["local_history_after_close"]
    expired_local_after = expired_quiescence["local_workflow_after"]
    expired_local_result = expired_quiescence["local_result"]
    close_binding = expired_quiescence["close_binding"]
    close_payload = expired_quiescence["close_payload"]
    close_receipt = expired_quiescence["close_receipt"]
    close_signal_events = [expired_quiescence["close_signal_event"]]
    close_activity_scheduled = expired_quiescence["close_activity_scheduled_event"]
    close_activity_completed_events = [
        expired_quiescence["close_activity_completed_event"]
    ]
    close_worker_build_ids = expired_quiescence["close_worker_build_ids"]
    publication_submit_state_before = expired_quiescence[
        "publication_submit_state_before"
    ]
    publication_submit_state_during = expired_quiescence[
        "publication_submit_state_during"
    ]
    expired_local_history_before = expired_quiescence["local_history_before"]
    expired_cycle_state_before = expired_quiescence["cycle_state_before"]
    base_evidence["fixture_terminal_boundaries"]["expired_permit_receipt"] = {
        "cycle_id": expired_local_cycle_id,
        "local_workflow_id": expired_local_workflow_id,
        "local_workflow_status": expired_local_after.status.name,
        "local_workflow_run_id": expired_local_after.run_id,
        "local_workflow_history_last_event_id": max(
            event.event_id for event in expired_local_history_after.events
        ),
        "local_workflow_history_sha256": _temporal_history_sha256(
            expired_local_history_after.events
        ),
        "local_workflow_result": expired_local_result,
        "database_state_sha256": canonical_hash(expired_cycle_state_after),
        "cycle_events_sha256": canonical_hash(expired_cycle_state_after["cycle_events"]),
        "outbox_rows": expired_cycle_state_after["outbox_rows"],
        "outbox_row_count": len(expired_cycle_state_after["outbox_rows"]),
        "pending_outbox_rows": 0,
        "fixture_quiescence_close": {
            "message_id": close_binding["id"],
            "sequence": close_binding["sequence"],
            "kind": close_binding["kind"],
            "payload": close_binding["payload"],
            "delivery": "CycleOutbox.dispatch_one/TemporalCycleTransport/LocalCycleActivities.consume",
            "receipt_state": close_receipt["receipt_state"],
            "governed_cycle_state": expired_cycle_state_after["cycle"]["state"],
            "runtime_worker_stopped_before_hold_snapshot": True,
            "signal_event_id": close_signal_events[0].event_id,
            "consume_activity_scheduled_event_id": close_activity_scheduled.event_id,
            "consume_activity_completed_event_id": close_activity_completed_events[0].event_id,
            "worker_build_ids": sorted(close_worker_build_ids),
            "publication_submit_state_before_close": publication_submit_state_before,
            "publication_submit_state_after_close": publication_submit_state_during,
            "publication_submit_state_after_worker_exit": publication_submit_state_after,
            "submit_context": expired_quiescence["submit_context"],
            "accepted_receipt_absent_before_release": expired_quiescence[
                "accepted_receipt_absent_before_release"
            ],
            "accepted_receipt_state_after_release": accepted_receipt["receipt_state"],
            "worker_process_exit_code_after_acceptance": worker_process_exit_event["return_code"],
        },
        "liability_snapshot_sha256": canonical_hash(expired_terminal_boundary),
    }
    save_json(progress_path, base_evidence)
    expired_before_hold = _liability_snapshot(database, expired_operation_id)
    _assert_named_liabilities_unchanged(
        terminal_boundary_snapshots["expired_permit_receipt"], expired_before_hold
    )
    expired_hold = record_publication_recovery_hold(
        database,
        workspace_id=workspace_id,
        operation_id=expired_operation_id,
        reason="expired_permit_receipt",
    )
    expired_after_hold = _liability_snapshot(database, expired_operation_id)
    _assert_named_liabilities_unchanged(expired_before_hold, expired_after_hold)
    expired_hold_row = _recovery_hold_snapshot(database, expired_operation_id)
    assert expired_hold_row is not None
    assert expired_hold_row["reason"] == "expired_permit_receipt"
    assert _recovery_hold_count(database, expired_operation_id) == 1

    # Build the legacy preflight database at the schema floor that first
    # allowed these rows. Its liabilities are database fixtures only: neither
    # operation is delivered to an outbox worker or assigned Temporal history.
    legacy_database_name, legacy_database = _create_database(
        database_admin_url, "recovery_floor_legacy_"
    )
    request.addfinalizer(
        lambda: _drop_database(database_admin_url, legacy_database_name)
        if not persist_fixtures
        else None
    )
    legacy_0042 = _migrate(
        legacy_database, "upgrade", "0042_publication_recovery_holds"
    )
    assert legacy_0042.returncode == 0, legacy_0042.stderr
    with psycopg.connect(legacy_database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "0042_publication_recovery_holds"
    monkeypatch.setenv("TEST_LEGACY_NULL_DATABASE_URL", legacy_database)

    import test_creative_release_gate_migration as release_gate

    monkeypatch.setenv("TEST_DATABASE_URL", legacy_database)
    try:
        legacy_null_fixture = await _publication_fixture(legacy_database)
    finally:
        monkeypatch.setenv("TEST_DATABASE_URL", database)
    original_ready_package = release_gate._approved_ready_package

    async def reuse_legacy_ready_package(*, publication_proofs=True):
        return legacy_null_fixture["ready"]

    monkeypatch.setattr(
        release_gate, "_approved_ready_package", reuse_legacy_ready_package
    )
    monkeypatch.setenv("TEST_DATABASE_URL", legacy_database)
    try:
        legacy_companion_fixture = await _publication_fixture(legacy_database)
    finally:
        monkeypatch.setenv("TEST_DATABASE_URL", database)
        monkeypatch.setattr(
            release_gate, "_approved_ready_package", original_ready_package
        )
    assert (
        legacy_companion_fixture["workspace_id"]
        == legacy_null_fixture["workspace_id"]
    )
    legacy_null_submitted = legacy_null_fixture["bridge"].submit_publication(
        legacy_null_fixture["command"]
    )
    legacy_companion_submitted = legacy_companion_fixture["bridge"].submit_publication(
        legacy_companion_fixture["command"]
    )
    legacy_null_operation_id = UUID(legacy_null_submitted["operation_id"])
    legacy_companion_operation_id = UUID(legacy_companion_submitted["operation_id"])
    assert legacy_null_operation_id not in {expired_operation_id, cancelled_operation_id}
    assert legacy_companion_operation_id not in {
        legacy_null_operation_id,
        expired_operation_id,
        cancelled_operation_id,
    }

    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from salience.cycles.governance import CycleGovernance, PermitRequest
    from salience.publication.contracts import RemotePublicationReceipt
    from salience.publication.providers import FixturePublisherAdapter
    from salience.publication.repository import PublicationRepository

    async def seed_preflight_liability_graph(
        fixture, submitted, *, permit_ttl_seconds
    ):
        operation_id = UUID(submitted["operation_id"])
        with psycopg.connect(legacy_database, row_factory=dict_row) as connection:
            binding = connection.execute(
                """SELECT binding.*, intent.goal_revision
                   FROM v4_legacy_dispatches binding
                   JOIN v4_cycle_intents intent ON intent.id=binding.intent_id
                   WHERE binding.operation_id=%s""",
                (str(operation_id),),
            ).fetchone()
        assert binding is not None

        from salience.cycles.legacy_publication import native_publication_request

        workflow_request = native_publication_request(binding)
        repository = PublicationRepository(legacy_database)
        persisted_request = await repository.create_request(
            ready_package_id=workflow_request.ready_package_id,
            workspace_id=workflow_request.workspace_id,
            content_program_id=workflow_request.content_program_id,
            publisher_account_id=workflow_request.publisher_account_id,
            publication_approval_request_id=(
                workflow_request.publication_approval_request_id
            ),
            idempotency_key=workflow_request.idempotency_key,
            platform=workflow_request.platform,
            destination=workflow_request.destination,
            locale=workflow_request.locale,
            territory=workflow_request.territory,
            visibility=workflow_request.visibility,
            capability_profile_version=workflow_request.capability_profile_version,
        )
        publication_request = await repository.load_request(persisted_request.id)
        plan = await repository.create_plan(
            publication_request_id=publication_request.id,
            version=1,
            publisher_id=publication_request.publisher_id or "fixture-publisher",
            publisher_version=FixturePublisherAdapter().capabilities.version,
            external_effect_id=None,
            trace_id=None,
            span_id=None,
        )
        attempt = await repository.create_attempt(
            publication_plan_id=plan.id,
            attempt_number=1,
            idempotency_key=publication_request.idempotency_key + ":attempt:1",
        )

        request_payload = publication_request.model_dump(mode="json")
        remote_id = "fixture-v4-publication:" + str(operation_id)
        safe_metadata_hash = hashlib.sha256(
            json.dumps(request_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        fixture_receipt = RemotePublicationReceipt(
            id="fixture-v4-receipt:" + str(operation_id),
            publication_attempt_id=publication_request.id,
            publisher_id="fixture-publisher",
            remote_id=remote_id,
            state="accepted",
            safe_metadata_hash=safe_metadata_hash,
            remote_url="https://fixture.invalid/publications/" + str(operation_id),
        )
        with psycopg.connect(legacy_database) as connection:
            request_fingerprint = connection.execute(
                "SELECT encode(digest(%s::jsonb::text,'sha256'),'hex')",
                (Jsonb(request_payload),),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO v4_legacy_publication_fixture_receipts
                   (operation_id,workspace_id,idempotency_key,fingerprint,remote_id,
                    request,receipt)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (
                    operation_id,
                    fixture["workspace_id"],
                    "v4-legacy:" + str(operation_id),
                    request_fingerprint,
                    remote_id,
                    Jsonb(request_payload),
                    Jsonb(fixture_receipt.model_dump(mode="json")),
                ),
            )
            connection.execute(
                """INSERT INTO v4_legacy_publication_fixture_progress
                   (operation_id,state) VALUES(%s,'accepted')""",
                (operation_id,),
            )

        await repository.record_remote_receipt(
            publication_attempt_id=attempt.id,
            publisher_id=fixture_receipt.publisher_id,
            remote_id=fixture_receipt.remote_id,
            state=fixture_receipt.state,
            safe_metadata_hash=fixture_receipt.safe_metadata_hash,
            remote_url=fixture_receipt.remote_url,
        )
        await repository.record_status_event(
            publication_attempt_id=attempt.id,
            state="accepted",
            source="submission",
            safe_payload_hash=safe_metadata_hash,
        )

        governance = CycleGovernance(
            legacy_database,
            workspace_id=fixture["workspace_id"],
            subject_id=fixture["actor_id"],
        )
        permit_request = PermitRequest(
            context_id=binding["context_id"],
            operation_id=operation_id,
            expected_goal_revision=binding["goal_revision"],
            account_ref="fixture-account",
            purpose="fixture_execution",
            effect="fixture.noop",
            artifact_sha256=hashlib.sha256(
                ("task8-preflight:" + str(operation_id)).encode("utf-8")
            ).hexdigest(),
            ttl_seconds=permit_ttl_seconds,
        )
        issued = governance.issue_permit(
            binding["cycle_id"],
            permit_request,
            idempotency_key="task8-preflight-permit:" + str(operation_id),
        )
        claimed = governance.claim_permit(issued["permit_id"])
        assert claimed["dispatch_allowed"] is True
        with psycopg.connect(legacy_database) as connection:
            started_job = connection.execute(
                """UPDATE jobs SET state='running',
                          started_at=COALESCE(started_at,clock_timestamp())
                   WHERE id=%s AND state='queued' RETURNING id""",
                (binding["job_id"],),
            ).fetchone()
        assert started_job == (binding["job_id"],)
        return {
            "operation_id": operation_id,
            "permit_id": UUID(issued["permit_id"]),
            "publication_request_id": UUID(publication_request.id),
            "publication_attempt_id": UUID(attempt.id),
        }

    legacy_null_graph = await seed_preflight_liability_graph(
        legacy_null_fixture, legacy_null_submitted, permit_ttl_seconds=60
    )
    legacy_companion_graph = await seed_preflight_liability_graph(
        legacy_companion_fixture,
        legacy_companion_submitted,
        permit_ttl_seconds=1,
    )
    assert legacy_null_graph["operation_id"] == legacy_null_operation_id
    assert legacy_companion_graph["operation_id"] == legacy_companion_operation_id
    with psycopg.connect(legacy_database) as connection:
        seeded_receipt = connection.execute(
            """SELECT receipt_row.operation_id, progress.state,
                      receipt_row.idempotency_key, receipt_row.remote_id,
                      receipt_row.receipt->>'state'
               FROM v4_legacy_publication_fixture_receipts receipt_row
               JOIN v4_legacy_publication_fixture_progress progress USING(operation_id)
               WHERE receipt_row.operation_id=%s""",
            (legacy_null_operation_id,),
        ).fetchone()
    assert seeded_receipt == (
        legacy_null_operation_id,
        "accepted",
        "v4-legacy:" + str(legacy_null_operation_id),
        "fixture-v4-publication:" + str(legacy_null_operation_id),
        "accepted",
    )

    with psycopg.connect(legacy_database) as connection:
        published_progress = connection.execute(
            """UPDATE v4_legacy_publication_fixture_progress
               SET state='published', polls=2, updated_at=clock_timestamp()
               WHERE operation_id=%s AND state='accepted'
               RETURNING operation_id,state""",
            (legacy_null_operation_id,),
        ).fetchone()
    assert published_progress == (legacy_null_operation_id, "published")
    legacy_null_fixture["bridge"].close(
        legacy_null_submitted["cycle_id"],
        disposition="cancelled",
        reason=(
            "Preserve accepted publication liability for the legacy NULL-v1 preflight fixture"
        ),
    )
    await _wait_for_publication_permit_expiry(
        legacy_database, str(legacy_companion_operation_id)
    )

    legacy_cancel_before = _liability_snapshot(
        legacy_database, legacy_null_operation_id
    )
    legacy_companion_before_hold = _liability_snapshot(
        legacy_database, legacy_companion_operation_id
    )
    with psycopg.connect(legacy_database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "0042_publication_recovery_holds"
        connection.execute(
            _INSERT_SQL,
            _hold_values(
                connection,
                legacy_null_operation_id,
                reason="published_during_cancel",
            ),
        )
        legacy_0042_hold_row = connection.execute(
            "SELECT to_jsonb(hold) FROM v4_legacy_publication_recovery_holds hold "
            "WHERE operation_id=%s",
            (legacy_null_operation_id,),
        ).fetchone()[0]
    assert legacy_0042_hold_row["reason"] == "published_during_cancel"
    assert _recovery_hold_count(legacy_database, legacy_null_operation_id) == 1
    _assert_named_liabilities_unchanged(
        legacy_cancel_before,
        _liability_snapshot(legacy_database, legacy_null_operation_id),
    )
    assert _recovery_hold_count(legacy_database, legacy_companion_operation_id) == 0

    upgraded_legacy = _migrate(
        legacy_database, "upgrade", "0043_complete_publication_recovery_liabilities"
    )
    assert upgraded_legacy.returncode == 0, upgraded_legacy.stderr
    with psycopg.connect(legacy_database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "0043_complete_publication_recovery_liabilities"
        upgraded_null_hold = connection.execute(
            "SELECT to_jsonb(hold) FROM v4_legacy_publication_recovery_holds hold "
            "WHERE operation_id=%s",
            (legacy_null_operation_id,),
        ).fetchone()[0]
        assert {
            key: upgraded_null_hold[key] for key in legacy_0042_hold_row
        } == legacy_0042_hold_row
        assert (
            upgraded_null_hold["liability_snapshot_version"],
            upgraded_null_hold["liability_snapshot_v1"],
            upgraded_null_hold["liability_fingerprint_v1"],
        ) == (None, None, None)
        with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
            connection.execute(
                "DELETE FROM v4_legacy_publication_recovery_holds "
                "WHERE operation_id=%s",
                (legacy_null_operation_id,),
            )
        after_rejected_delete = connection.execute(
            "SELECT to_jsonb(hold) FROM v4_legacy_publication_recovery_holds hold "
            "WHERE operation_id=%s",
            (legacy_null_operation_id,),
        ).fetchone()[0]
        assert after_rejected_delete == upgraded_null_hold
    _assert_named_liabilities_unchanged(
        legacy_cancel_before,
        _liability_snapshot(legacy_database, legacy_null_operation_id),
    )

    legacy_v1_companion = record_publication_recovery_hold(
        legacy_database,
        workspace_id=legacy_companion_fixture["workspace_id"],
        operation_id=legacy_companion_operation_id,
        reason="expired_permit_receipt",
    )
    legacy_companion_after_hold = _liability_snapshot(
        legacy_database, legacy_companion_operation_id
    )
    _assert_named_liabilities_unchanged(
        legacy_companion_before_hold, legacy_companion_after_hold
    )
    legacy_companion_row = _recovery_hold_snapshot(
        legacy_database, legacy_companion_operation_id
    )
    assert legacy_companion_row is not None
    assert legacy_companion_row["id"] == str(legacy_v1_companion.id)
    assert legacy_companion_row["operation_id"] == str(
        legacy_companion_operation_id
    )
    assert legacy_companion_row["reason"] == "expired_permit_receipt"
    assert legacy_companion_row["liability_snapshot_version"] == (
        "salience.publication-liability.v1"
    )
    assert len(legacy_companion_row["liability_snapshot_v1"]) == 21
    assert _recovery_hold_count(legacy_database, legacy_companion_operation_id) == 1
    with psycopg.connect(legacy_database) as connection:
        legacy_hold_counts = connection.execute(
            """SELECT count(*),
                      count(*) FILTER (WHERE liability_snapshot_version IS NULL),
                      count(*) FILTER (WHERE liability_snapshot_version IS NOT NULL)
               FROM v4_legacy_publication_recovery_holds"""
        ).fetchone()
    assert legacy_hold_counts == (2, 1, 1)
    with psycopg.connect(legacy_database) as connection:
        legacy_outbox_rows = connection.execute(
            """SELECT message.kind,message.state,message.attempts
               FROM v4_cycle_outbox message
               JOIN v4_cycles cycle ON cycle.id=message.cycle_id
               WHERE cycle.operation_id=ANY(%s)
               ORDER BY message.kind,message.sequence""",
            ([legacy_null_operation_id, legacy_companion_operation_id],),
        ).fetchall()
        legacy_inbox_count = connection.execute(
            """SELECT count(*)
               FROM v4_cycle_inbox inbox
               JOIN v4_cycles cycle ON cycle.id=inbox.cycle_id
               WHERE cycle.operation_id=ANY(%s)""",
            ([legacy_null_operation_id, legacy_companion_operation_id],),
        ).fetchone()[0]
    assert legacy_outbox_rows == [
        ("close", "pending", 0),
        ("start", "pending", 0),
        ("start", "pending", 0),
    ]
    assert legacy_inbox_count == 0
    monkeypatch.setenv("TEST_LEGACY_NULL_DATABASE_URL", legacy_database)

    operation_ids = {
        "expired_permit_receipt": expired_operation_id,
        "published_during_cancel": cancelled_operation_id,
    }
    expected_hold_ids = {
        "expired_permit_receipt": str(expired_hold.id),
        "published_during_cancel": cancelled_hold_row["id"],
    }
    before_snapshots = {
        "expired_permit_receipt": expired_after_hold,
        "published_during_cancel": cancel_after_hold,
    }
    first_views = verify_publication_recovery_rollback(
        database, workspace_id=workspace_id, operation_ids=operation_ids
    )
    assert all(len(snapshot) == 21 for snapshot in before_snapshots.values())
    first_verifier = {
        "workspace_id": str(workspace_id),
        "reasons": ["expired_permit_receipt", "published_during_cancel"],
        "operation_ids": {key: str(value) for key, value in operation_ids.items()},
        "hold_ids": {"expired_permit_receipt": str(first_views[0].id), "published_during_cancel": str(first_views[1].id)},
        "verified": True,
        "exit_code": 0,
    }
    assert first_verifier["hold_ids"] == expected_hold_ids

    expected_projection_counts = {
        reason: {
            "remote_receipts": len(snapshot["remote_publication_receipts"] or []),
            "status_events": len(snapshot["publication_status_events"] or []),
            "checkpoints": len(snapshot["job_checkpoints"] or []),
            "accepted_checkpoints": sum(
                item["checkpoint_payload"].get("checkpoint") == "publication.accepted"
                for item in snapshot["job_checkpoints"] or []
            ),
            "recovery_checkpoints": sum(
                item["checkpoint_payload"].get("checkpoint")
                == "publication.receipt_recovered_requires_typed_recovery"
                for item in snapshot["job_checkpoints"] or []
            ),
            "audit_events": len(snapshot["audit_events"] or []),
            "provenance_records": len(snapshot["provenance_records"] or []),
        }
        for reason, snapshot in before_snapshots.items()
    }
    before_hold_rows = {
        "expired_permit_receipt": expired_hold_row,
        "published_during_cancel": cancelled_hold_row,
    }
    base_evidence.update(
        {
            "workspace_id": str(workspace_id),
            "operation_ids": {key: str(value) for key, value in operation_ids.items()},
            "hold_ids": expected_hold_ids,
            "first_verifier": first_verifier,
            "liability_snapshot_hashes": {
                reason: canonical_hash(snapshot) for reason, snapshot in before_snapshots.items()
            },
            "hold_row_hashes": {
                reason: canonical_hash(row) for reason, row in before_hold_rows.items()
            },
            "projection_counts_before": expected_projection_counts,
        }
    )
    save_json(progress_path, base_evidence)

    # Both setup workers hard-exited at their intended boundaries. Confirm that
    # no prior qualified-image worker is still running before restart.
    with worker_harness._without_shared_checkout():
        listed = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={expected_image_id}", "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            check=True,
            cwd=tmp_path,
            env=worker_harness._docker_environment(),
            timeout=15,
        )
        image_container_ids = listed.stdout.splitlines()
        image_containers = []
        for container_id in image_container_ids:
            inspected = subprocess.run(
                ["docker", "inspect", container_id],
                capture_output=True,
                text=True,
                check=True,
                cwd=tmp_path,
                env=worker_harness._docker_environment(),
                timeout=15,
            )
            image_containers.append(json.loads(inspected.stdout)[0])
    classified_containers = []
    for container in image_containers:
        config = container.get("Config") or {}
        labels = config.get("Labels") or {}
        is_recorder = worker_harness.is_task8_adapter_recorder_container(
            container, expected_image_id=expected_image_id
        )
        classified_containers.append(
            {
                "container_id": container["Id"],
                "container_name": container["Name"].lstrip("/"),
                "image_id": container["Image"],
                "recorder_role": is_recorder,
                "entrypoint": config.get("Entrypoint"),
                "command": config.get("Cmd"),
                "compose_project": labels.get("com.docker.compose.project"),
                "compose_service": labels.get("com.docker.compose.service"),
            }
        )
    recorder_containers = [
        container for container in classified_containers if container["recorder_role"]
    ]
    running = [
        container for container in classified_containers if not container["recorder_role"]
    ]
    recorder_role_evidence = {
        "schema_version": "salience.task8-recorder-role-exclusion.v1",
        "application_commit": application_commit,
        "image_ref": image_ref,
        "image_id": expected_image_id,
        "docker_ps_command": [
            "docker",
            "ps",
            "--filter",
            f"ancestor={expected_image_id}",
            "--format",
            "{{.ID}}",
        ],
        "candidate_image_running_containers": classified_containers,
        "recorder_container_ids": [item["container_id"] for item in recorder_containers],
        "other_running_candidate_image_containers": running,
        "worker_guard_passed": len(recorder_containers) == 1 and running == [],
    }
    save_json(
        candidate_root / f"task8-recorder-role-exclusion-{candidate_suffix}.json",
        recorder_role_evidence,
    )
    base_evidence["recorder_role_exclusion"] = recorder_role_evidence
    base_evidence["known_running_image_workers_before_restart"] = running
    save_json(progress_path, base_evidence)
    assert len(recorder_containers) == 1, recorder_role_evidence
    assert running == [], recorder_role_evidence

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    recorder_base_url = os.environ["TEST_ADAPTER_RECORDER_URL"].rstrip("/")
    worker_cases = (
        ("expired_permit_receipt", expired_fixture["queue"], expired_operation_id),
        ("published_during_cancel", cancelled_fixture["queue"], cancelled_operation_id),
    )
    for reason, task_queue, operation_id in worker_cases:
        workflow_id = "salience-v4-legacy-operation:" + str(operation_id)
        handle = client.get_workflow_handle(workflow_id)
        workflow_before_restart = await handle.describe()
        assert workflow_before_restart.status == WorkflowExecutionStatus.RUNNING
        history_before_restart = await handle.fetch_history()
        event_id_before_restart = max(
            (event.event_id for event in history_before_restart.events), default=0
        )
        name = "publication-floor-" + reason.replace("_", "-") + "-" + uuid4().hex[:10]
        recorder_run_id = str(uuid4())
        recorder_worker_id = str(uuid4())
        recorder_events_before = _read_adapter_recorder_events(
            recorder_base_url, recorder_run_id
        )
        assert recorder_events_before == []
        attempt_record_path = candidate_root / f"task8-worker-retry-attempt-{reason}.json"
        attempt_record = {
            "schema_version": "salience.task8-worker-retry-attempt.v1",
            "application_commit": application_commit,
            "image_id": expected_image_id,
            "reason": reason,
            "task_queue": task_queue,
            "operation_id": str(operation_id),
            "hold_id": expected_hold_ids[reason],
            "workflow_id": workflow_id,
            "workflow_run_id": workflow_before_restart.run_id,
            "history_event_id_before_restart": event_id_before_restart,
            "history_sha256_before_restart": hashlib.sha256(
                b"".join(
                    event.SerializeToString(deterministic=True)
                    for event in history_before_restart.events
                )
            ).hexdigest(),
            "container_name": name,
            "recorder_run_id": recorder_run_id,
            "recorder_worker_id": recorder_worker_id,
            "recorder_events_before": recorder_events_before,
            "phase": "ready-to-launch",
        }
        save_json(attempt_record_path, attempt_record)
        launch = worker_harness.start_rollback_worker(
            image=expected_image_id,
            database_url=database,
            temporal_target=os.environ["TEST_TEMPORAL_TARGET"],
            workspace_id=str(workspace_id),
            expired_operation_id=str(expired_operation_id),
            cancel_operation_id=str(cancelled_operation_id),
            task_queue=task_queue,
            control_bundle=control_bundle,
            name=name,
            adapter_recorder_url=recorder_base_url,
            adapter_recorder_run_id=recorder_run_id,
            adapter_recorder_worker_id=recorder_worker_id,
        )
        run_evidence = {
            "reason": reason,
            "task_queue": task_queue,
            "operation_id": str(operation_id),
            "hold_id": expected_hold_ids[reason],
            "container_id": launch["container_id"],
            "container_name": name,
            "image_id": launch["image_id"],
            "launcher_argv": json.loads(launch["launcher_argv"]),
            "effects_enabled": False,
            "fixture_mode": True,
        }
        base_evidence["rollback_argv"] = json.loads(launch["launcher_argv"])
        try:
            inspected = worker_harness.inspect_worker(name)
            run_evidence["container_inspection"] = inspected
            assert inspected["image_id"] == expected_image_id
            assert inspected["path"] == "/usr/local/bin/python"
            assert inspected["args"] == [
                "-I",
                "-B",
                "-S",
                "-c",
                worker_harness._ADAPTER_RECORDER_LAUNCH_SHIM,
                "/run/salience-control/launch-v4-publication-worker.py",
                "--mode",
                "rollback-restart",
                "--worker-kind",
                "legacy-fixture",
                "--workspace-id",
                str(workspace_id),
                "--expired-hold-operation-id",
                str(expired_operation_id),
                "--cancel-hold-operation-id",
                str(cancelled_operation_id),
            ]
            assert inspected["state"] == "running"
            assert inspected["environment"]["SALIENCE_DEPLOYMENT_MODE"] == "fixture"
            assert inspected["environment"]["SALIENCE_EFFECTS_ENABLED"] == "false"
            assert inspected["environment"]["V4_LEGACY_FIXTURE_QUEUE"] == task_queue
            assert inspected["environment"]["V4_FIXTURE_WORKSPACE"] == str(workspace_id)
            mounted = {item["destination"]: item["read_write"] for item in inspected["mounts"]}
            assert mounted == {
                "/run/salience-control/launch-v4-publication-worker.py": False,
                "/run/salience-control/publication-worker-launch-lock.json": False,
                "/run/salience-control/publication-worker-launch-lock.sha256": False,
                "/run/task7-adapter-operation-recorder.py": False,
            }
            with pytest.raises(WorkflowFailureError) as failure:
                await asyncio.wait_for(handle.result(), timeout=45)
            typed_holds = [
                error
                for error in _exception_chain(failure.value)
                if getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
            ]
            assert len(typed_holds) == 1
            assert typed_holds[0].details == ({"hold_id": expected_hold_ids[reason]},)
            run_evidence["workflow_status"] = (await handle.describe()).status.name
            assert run_evidence["workflow_status"] == "FAILED"
            run_evidence["typed_failure"] = {
                "type": "LegacyPublicationRecoveryHeld",
                "details": [{"hold_id": expected_hold_ids[reason]}],
            }
            history_after_restart = await handle.fetch_history()
            scheduled_activity_types = {
                event.event_id: event.activity_task_scheduled_event_attributes.activity_type.name
                for event in history_after_restart.events
                if event.HasField("activity_task_scheduled_event_attributes")
            }
            restarted_activity_types = [
                scheduled_activity_types[event.activity_task_started_event_attributes.scheduled_event_id]
                for event in history_after_restart.events
                if event.event_id > event_id_before_restart
                and event.HasField("activity_task_started_event_attributes")
                and event.activity_task_started_event_attributes.scheduled_event_id
                in scheduled_activity_types
            ]
            expected_activity_type = {
                "expired_permit_receipt": "salience.publication.submit_or_reconcile",
                "published_during_cancel": "salience.publication.cancel",
            }[reason]
            assert restarted_activity_types
            assert set(restarted_activity_types) == {expected_activity_type}
            assert "salience.publication.await" not in restarted_activity_types
            run_evidence["restarted_activity_types"] = restarted_activity_types
        finally:
            stopped = worker_harness.stop_worker(name)
            logs_path = candidate_root / f"{reason}-worker.log"
            worker_log_text = safe_text(str(stopped.get("logs", "")))
            logs_path.write_text(worker_log_text, encoding="utf-8")
            recorder_events_after = _read_adapter_recorder_events(
                recorder_base_url, recorder_run_id
            )
            run_evidence["adapter_operation_recorder"] = {
                "run_id": recorder_run_id,
                "worker_id": recorder_worker_id,
                "events_before": recorder_events_before,
                "events_after": recorder_events_after,
                "observed_operation_count": len(recorder_events_after),
                "zero_operations": recorder_events_after == recorder_events_before == [],
            }
            run_evidence["stop"] = {
                "state": stopped.get("state"),
                "logs_path": str(logs_path.relative_to(evidence_root)),
                "logs_exit_code": stopped.get("logs_exit_code"),
            }
            attempt_record.update(
                {
                    "container_id": launch["container_id"],
                    "container_inspection": run_evidence.get("container_inspection"),
                    "recorder_readback": run_evidence["adapter_operation_recorder"],
                    "worker_log_sha256": hashlib.sha256(
                        logs_path.read_bytes()
                    ).hexdigest(),
                    "stop": run_evidence["stop"],
                    "phase": "worker-stopped-and-recorder-read-back",
                }
            )
            save_json(attempt_record_path, attempt_record)
        assert run_evidence["stop"]["state"] == "exited"
        expected_hold_frame = {
            "expired_permit_receipt": "in submit_or_reconcile",
            "published_during_cancel": "in cancel",
        }[reason]
        expected_hold_diagnostic = {
            "expired_permit_receipt": "durable publication receipt recovered; typed execution recovery is required",
            "published_during_cancel": "published publication readback during cancellation requires typed execution recovery",
        }[reason]
        assert "legacy_publication_runtime.py" in worker_log_text
        assert expected_hold_frame in worker_log_text
        assert expected_hold_diagnostic in worker_log_text
        assert "salience.publication.await" not in worker_log_text
        assert run_evidence["adapter_operation_recorder"]["zero_operations"] is True
        base_evidence["worker_runs"].append(run_evidence)
        save_json(progress_path, base_evidence)

    after_snapshots = {
        "expired_permit_receipt": _liability_snapshot(database, expired_operation_id),
        "published_during_cancel": _liability_snapshot(database, cancelled_operation_id),
    }
    assert all(len(snapshot) == 21 for snapshot in after_snapshots.values())
    after_hold_rows = {
        "expired_permit_receipt": _recovery_hold_snapshot(database, expired_operation_id),
        "published_during_cancel": _recovery_hold_snapshot(database, cancelled_operation_id),
    }
    projection_counts_after = {}
    for reason in operation_ids:
        _assert_named_liabilities_unchanged(before_snapshots[reason], after_snapshots[reason])
        assert _recovery_hold_count(database, operation_ids[reason]) == 1
        assert after_hold_rows[reason] == before_hold_rows[reason]
        counts = _recovery_projection_counts(database, operation_ids[reason])
        assert counts == expected_projection_counts[reason]
        projection_counts_after[reason] = counts
        fixture_progress_before = before_snapshots[reason]["fixture_progress"]
        fixture_progress_after = after_snapshots[reason]["fixture_progress"]
        assert fixture_progress_after == fixture_progress_before
        run_evidence = next(
            item for item in base_evidence["worker_runs"] if item["reason"] == reason
        )
        run_evidence["provider_call_instrumentation"] = {
            "neutral_adapter_operation_recorder": {
                "run_id": run_evidence["adapter_operation_recorder"]["run_id"],
                "worker_id": run_evidence["adapter_operation_recorder"]["worker_id"],
                "events_before": run_evidence["adapter_operation_recorder"]["events_before"],
                "events_after": run_evidence["adapter_operation_recorder"]["events_after"],
                "observed_operation_count": run_evidence["adapter_operation_recorder"]["observed_operation_count"],
                "zero_operations": run_evidence["adapter_operation_recorder"]["zero_operations"],
            },
            "restarted_activity_types": run_evidence["restarted_activity_types"],
            "typed_hold_frame": {
                "expired_permit_receipt": "in submit_or_reconcile",
                "published_during_cancel": "in cancel",
            }[reason],
            "worker_log_sha256": hashlib.sha256(
                (candidate_root / f"{reason}-worker.log").read_bytes()
            ).hexdigest(),
            "fixture_progress_before": fixture_progress_before,
            "fixture_progress_after": fixture_progress_after,
            "basis": "A fresh independent receiver was started before this exact pending activity retry; its readback contains zero protocol operations, and the fixture progress plus full liabilities are unchanged.",
        }

    base_evidence["projection_counts_after"] = projection_counts_after
    save_json(progress_path, base_evidence)

    second_views = verify_publication_recovery_rollback(
        database, workspace_id=workspace_id, operation_ids=operation_ids
    )
    second_verifier = {
        "workspace_id": str(workspace_id),
        "reasons": ["expired_permit_receipt", "published_during_cancel"],
        "operation_ids": {key: str(value) for key, value in operation_ids.items()},
        "hold_ids": {
            "expired_permit_receipt": str(second_views[0].id),
            "published_during_cancel": str(second_views[1].id),
        },
        "verified": True,
        "exit_code": 0,
    }
    assert second_verifier == first_verifier
    base_evidence["second_verifier"] = second_verifier
    base_evidence["liability_snapshot_hashes_after"] = {
        reason: canonical_hash(snapshot) for reason, snapshot in after_snapshots.items()
    }
    base_evidence["hold_row_hashes_after"] = {
        reason: canonical_hash(row) for reason, row in after_hold_rows.items()
    }
    assert base_evidence["liability_snapshot_hashes_after"] == base_evidence[
        "liability_snapshot_hashes"
    ]
    assert base_evidence["hold_row_hashes_after"] == base_evidence["hold_row_hashes"]

    legacy_database = os.environ["TEST_LEGACY_NULL_DATABASE_URL"]
    recorder_base_url = os.environ["TEST_ADAPTER_RECORDER_URL"]
    recorder_run_id = str(UUID(os.environ["TEST_ADAPTER_RECORDER_RUN_ID"]))
    temporal_target = os.environ["TEST_TEMPORAL_TARGET"]

    def target_record(database_url):
        from urllib.parse import urlsplit

        parts = urlsplit(database_url)
        assert parts.scheme in {"postgresql", "postgres"}
        assert parts.hostname and parts.path.lstrip("/")
        return {
            "database_name": parts.path.lstrip("/"),
            "database_host": parts.hostname,
            "database_port": parts.port or 5432,
            "temporal_target": temporal_target,
            "adapter_recorder": {
                "base_url": recorder_base_url,
                "run_id": recorder_run_id,
            },
            "fixture_lifetime": "task8-persistent",
        }

    async def capture_history_fixture(target, operation_ids, task_queues=None):
        client = await Client.connect(target)
        history_fixture = {}
        for reason, operation_id in operation_ids.items():
            workflow_id = "salience-v4-legacy-operation:" + str(UUID(operation_id))
            history = await client.get_workflow_handle(workflow_id).fetch_history()
            events = history.events
            start_event = next(
                event for event in events
                if event.HasField("workflow_execution_started_event_attributes")
            )
            task_queue = start_event.workflow_execution_started_event_attributes.task_queue.name
            if task_queues is not None:
                assert task_queue == task_queues[reason]
            history_fixture[reason] = {
                "workflow_id": workflow_id,
                "task_queue": task_queue,
                "last_event_id": max((event.event_id for event in events), default=0),
                "history_sha256": _temporal_history_sha256(events),
            }
        return history_fixture

    def read_history_fixture(target, expected):
        operation_ids_for_history = {
            reason: metadata["workflow_id"].removeprefix(
                "salience-v4-legacy-operation:"
            )
            for reason, metadata in expected.items()
        }
        task_queues = {reason: metadata["task_queue"] for reason, metadata in expected.items()}
        observed = capture_history_fixture(target, operation_ids_for_history, task_queues)

        async def record_and_return():
            observed_histories = await observed
            if observed_histories != expected:
                save_json(
                    candidate_root
                    / f"task8-history-readback-mismatch-{candidate_suffix}.json",
                    {"expected": expected, "observed": observed_histories},
                )
            return observed_histories

        return record_and_return()

    def read_database_state(database_url, state_workspace_id, state_operation_ids):
        with psycopg.connect(database_url) as connection:
            head = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchall()
        assert head == [("0043_complete_publication_recovery_liabilities",)]
        holds = {
            reason: _recovery_hold_snapshot(database_url, UUID(operation_id))
            for reason, operation_id in state_operation_ids.items()
        }
        liabilities = {
            reason: _liability_snapshot(database_url, UUID(operation_id))
            for reason, operation_id in state_operation_ids.items()
        }
        assert all(row is not None for row in holds.values())
        return {
            "head": "0043_complete_publication_recovery_liabilities",
            "workspace_id": str(UUID(state_workspace_id)),
            "holds": holds,
            "liabilities": liabilities,
        }

    primary_operation_ids = {
        reason: str(operation_id) for reason, operation_id in operation_ids.items()
    }
    primary_hold_ids = {
        reason: str(row["id"]) for reason, row in before_hold_rows.items()
    }
    primary_workspace_id = str(workspace_id)
    assert UUID(primary_workspace_id) == workspace_id
    primary_targets = target_record(database)
    primary_histories = await capture_history_fixture(
        temporal_target,
        primary_operation_ids,
        {
            "expired_permit_receipt": expired_fixture["queue"],
            "published_during_cancel": cancelled_fixture["queue"],
        },
    )
    primary_state = read_database_state(database, primary_workspace_id, primary_operation_ids)
    primary_fixture = {
        "schema_version": "salience.publication-floor-rollback-launch-fixture.v1",
        "application_commit": application_commit,
        "image_ref": image_ref,
        "image_id": expected_image_id,
        "launch_lock_sha256": trusted_digest,
        "disposable_targets": primary_targets,
        "workspace_id": primary_workspace_id,
        "operation_ids": primary_operation_ids,
        "hold_ids": primary_hold_ids,
        "history_fixture": primary_histories,
        "liability_snapshot_hashes": {
            reason: canonical_hash(primary_state["liabilities"][reason])
            for reason in primary_operation_ids
        },
        "hold_row_hashes": {
            reason: canonical_hash(primary_state["holds"][reason])
            for reason in primary_operation_ids
        },
    }

    with psycopg.connect(legacy_database) as connection:
        legacy_head = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
        legacy_rows = connection.execute(
            "SELECT to_jsonb(hold) FROM v4_legacy_publication_recovery_holds hold "
            "ORDER BY reason"
        ).fetchall()
        assert len(legacy_rows) == 2
        legacy_hold_rows = {row[0]["reason"]: row[0] for row in legacy_rows}
        assert set(legacy_hold_rows) == {"expired_permit_receipt", "published_during_cancel"}
        legacy_null_columns = connection.execute(
            "SELECT liability_snapshot_version, liability_snapshot_v1, "
            "liability_fingerprint_v1 FROM v4_legacy_publication_recovery_holds "
            "WHERE operation_id=%s",
            (legacy_hold_rows["published_during_cancel"]["operation_id"],),
        ).fetchone()
    assert legacy_head == [("0043_complete_publication_recovery_liabilities",)]
    legacy_workspace_id = str(UUID(legacy_hold_rows["expired_permit_receipt"]["workspace_id"]))
    assert all(
        str(UUID(row["workspace_id"])) == legacy_workspace_id
        for row in legacy_hold_rows.values()
    )
    assert legacy_null_columns == (None, None, None)
    legacy_operation_ids = {
        reason: str(UUID(row["operation_id"]))
        for reason, row in legacy_hold_rows.items()
    }
    legacy_hold_ids = {
        reason: str(UUID(row["id"])) for reason, row in legacy_hold_rows.items()
    }
    legacy_targets = target_record(legacy_database)
    legacy_liabilities = {
        reason: _liability_snapshot(legacy_database, UUID(operation_id))
        for reason, operation_id in legacy_operation_ids.items()
    }
    legacy_fixture = {
        "schema_version": "salience.publication-floor-legacy-null-v1-fixture.v1",
        "application_commit": application_commit,
        "image_ref": image_ref,
        "image_id": expected_image_id,
        "launch_lock_sha256": trusted_digest,
        "disposable_targets": legacy_targets,
        "workspace_id": legacy_workspace_id,
        "operation_ids": legacy_operation_ids,
        "hold_ids": legacy_hold_ids,
        "execution_mode": {
            "mode": "preflight_only",
            "denial_stage": "before_worker_registration",
            "worker_started": False,
            "outbox_dispatched": False,
        },
        "liability_snapshot_hashes": {
            reason: canonical_hash(legacy_liabilities[reason])
            for reason in legacy_operation_ids
        },
        "hold_row_hashes": {
            reason: canonical_hash(legacy_hold_rows[reason])
            for reason in legacy_operation_ids
        },
        "legacy_null_operation_id": legacy_operation_ids["published_during_cancel"],
    }

    def read_legacy_database_state():
        observed = read_database_state(legacy_database, legacy_workspace_id, legacy_operation_ids)
        with psycopg.connect(legacy_database) as connection:
            null_columns = connection.execute(
                "SELECT liability_snapshot_version, liability_snapshot_v1, "
                "liability_fingerprint_v1 FROM v4_legacy_publication_recovery_holds "
                "WHERE operation_id=%s",
                (legacy_operation_ids["published_during_cancel"],),
            ).fetchone()
            outbox_rows = connection.execute(
                """SELECT message.kind,message.state,message.attempts
                   FROM v4_cycle_outbox message
                   JOIN v4_cycles cycle ON cycle.id=message.cycle_id
                   WHERE cycle.operation_id=ANY(%s)
                   ORDER BY message.kind,message.sequence""",
                ([UUID(operation_id) for operation_id in legacy_operation_ids.values()],),
            ).fetchall()
            inbox_count = connection.execute(
                """SELECT count(*)
                   FROM v4_cycle_inbox inbox
                   JOIN v4_cycles cycle ON cycle.id=inbox.cycle_id
                   WHERE cycle.operation_id=ANY(%s)""",
                ([UUID(operation_id) for operation_id in legacy_operation_ids.values()],),
            ).fetchone()[0]
        assert outbox_rows == [
            ("close", "pending", 0),
            ("start", "pending", 0),
            ("start", "pending", 0),
        ]
        assert inbox_count == 0
        observed["legacy_null_v1"] = {
            "operation_id": legacy_operation_ids["published_during_cancel"],
            "fields": list(null_columns),
        }
        return observed

    def read_recorder_events(base_url, run_id):
        import urllib.request

        url = base_url.rstrip("/") + f"/v1/runs/{run_id}/events"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read())
        assert payload["run_id"] == run_id
        return payload["events"]

    async def publish_progress():
        await worker_harness.write_qualification_progress(
            candidate_root,
            rollback_launch_fixture=primary_fixture,
            legacy_null_v1_fixture=legacy_fixture,
            persistent_fixture=True,
            read_database_state=lambda: read_database_state(
                database, primary_fixture["workspace_id"], primary_operation_ids
            ),
            read_legacy_database_state=read_legacy_database_state,
            read_histories=read_history_fixture,
            read_recorder_events=read_recorder_events,
        )

    async def reset_terminal_history_for_recorder(reason):
        from temporalio.api.common.v1 import WorkflowExecution
        from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest

        workflow_id = primary_fixture["history_fixture"][reason]["workflow_id"]
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()
        assert description.status == WorkflowExecutionStatus.FAILED
        history = await handle.fetch_history()
        activity_name = {
            "expired_permit_receipt": "salience.publication.submit_or_reconcile",
            "published_during_cancel": "salience.publication.cancel",
        }[reason]
        scheduled_events = [
            event
            for event in history.events
            if event.HasField("activity_task_scheduled_event_attributes")
            and event.activity_task_scheduled_event_attributes.activity_type.name
            == activity_name
        ]
        assert len(scheduled_events) == 1, (
            f"expected exactly one original {activity_name} schedule for {reason}"
        )
        scheduled = scheduled_events[0]
        failed_events = [
            event
            for event in history.events
            if event.HasField("activity_task_failed_event_attributes")
            and event.activity_task_failed_event_attributes.scheduled_event_id
            == scheduled.event_id
        ]
        assert len(failed_events) == 1, (
            f"original {activity_name} did not persist exactly one terminal failure"
        )
        activity_failure = failed_events[0].activity_task_failed_event_attributes.failure
        assert activity_failure.HasField("application_failure_info")
        assert (
            activity_failure.application_failure_info.type
            == "LegacyPublicationRecoveryHeld"
        ), "reset source history does not contain the original typed hold receipt"
        workflow_tasks_after_failure = [
            event
            for event in history.events
            if event.event_id > failed_events[0].event_id
            and event.HasField("workflow_task_completed_event_attributes")
        ]
        assert workflow_tasks_after_failure, (
            "history has no completed workflow task handling the recorded hold failure"
        )
        reset_boundary = min(workflow_tasks_after_failure, key=lambda event: event.event_id)
        terminal_failures_after_boundary = [
            event
            for event in history.events
            if event.event_id > reset_boundary.event_id
            and event.HasField("workflow_execution_failed_event_attributes")
        ]
        assert terminal_failures_after_boundary, (
            "reset boundary must precede the original terminal workflow failure"
        )

        def activity_schedule_and_start_ids(events):
            return {
                "scheduled": [
                    event.event_id
                    for event in events
                    if event.HasField("activity_task_scheduled_event_attributes")
                ],
                "started": [
                    event.event_id
                    for event in events
                    if event.HasField("activity_task_started_event_attributes")
                ],
            }

        original_activity_events = activity_schedule_and_start_ids(history.events)
        reset_request = ResetWorkflowExecutionRequest(
            namespace="default",
            workflow_execution=WorkflowExecution(
                workflow_id=workflow_id, run_id=description.run_id
            ),
            reason="Task 8 workflow replay after the recorded hold activity failure",
            # This separate control replays the workflow task that consumes the
            # already-recorded non-retryable hold failure. The actual pending
            # activity retries are observed by the first rollback worker above.
            workflow_task_finish_event_id=reset_boundary.event_id,
            request_id=str(uuid4()),
            identity="p1-publication-recovery-task8",
        )
        await client.workflow_service.reset_workflow_execution(reset_request)
        restarted = client.get_workflow_handle(workflow_id)
        new_description = await restarted.describe()
        assert new_description.run_id != description.run_id
        assert new_description.status == WorkflowExecutionStatus.RUNNING
        reset_history = await restarted.fetch_history()
        reset_activity_events = activity_schedule_and_start_ids(reset_history.events)
        assert reset_activity_events == original_activity_events, (
            "workflow replay unexpectedly scheduled or started a new activity"
        )
        assert reset_boundary.event_id > failed_events[0].event_id
        assert terminal_failures_after_boundary, (
            "reset boundary must precede the original terminal workflow failure"
        )
        return restarted, {
            "reason": reason,
            "workflow_id": workflow_id,
            "source_run_id": description.run_id,
            "reset_run_id": new_description.run_id,
            "activity_name": activity_name,
            "activity_scheduled_event_id": scheduled.event_id,
            "activity_failed_event_id": failed_events[0].event_id,
            "activity_failure_type": activity_failure.application_failure_info.type,
            "reset_workflow_task_completed_event_id": reset_boundary.event_id,
            "terminal_workflow_failure_event_id": terminal_failures_after_boundary[0].event_id,
            "activity_schedule_and_start_event_ids_before": original_activity_events,
            "activity_schedule_and_start_event_ids_after_reset": reset_activity_events,
            "reset_boundary_invariant": (
                "first workflow-task completion after the original typed hold activity failure"
            ),
        }

    async def assert_quiescent_runtime_boundary(reason):
        boundary = base_evidence["fixture_terminal_boundaries"][reason]
        workflow_id = boundary["local_workflow_id"]
        runtime_handle = client.get_workflow_handle(workflow_id)
        runtime_description = await runtime_handle.describe()
        expected_status = {
            "expired_permit_receipt": WorkflowExecutionStatus.COMPLETED,
            "published_during_cancel": WorkflowExecutionStatus.COMPLETED,
        }[reason]
        assert runtime_description.status == expected_status, (
            f"{reason} local runtime is not quiescent: {runtime_description.status}"
        )
        runtime_history = await runtime_handle.fetch_history()
        assert max(event.event_id for event in runtime_history.events) == boundary[
            "local_workflow_history_last_event_id"
        ]
        assert _temporal_history_sha256(runtime_history.events) == boundary[
            "local_workflow_history_sha256"
        ], "a local runtime actor appended history after its terminal boundary"
        database_state = read_cycle_runtime_database_state(boundary["cycle_id"])
        assert canonical_hash(database_state) == boundary["database_state_sha256"]
        assert canonical_hash(database_state["cycle_events"]) == boundary[
            "cycle_events_sha256"
        ], "a local runtime actor appended a cycle event after the hold snapshot"
        assert database_state["runtime_holds"] == []
        assert all(
            row["state"] == "delivered" and row["receipt_state"] == "recorded"
            for row in database_state["outbox_rows"]
        ), "a pending runtime outbox could append a cycle event during replay"
        assert len(database_state["outbox_rows"]) == boundary["outbox_row_count"]

    async def run_conditional_cases():
        candidate_for_conditional_cases = _task8_rollback_image_or_skip(
            candidate_root=candidate_root,
            environment={
                "TEST_DATABASE_URL": database,
                "TEST_LEGACY_NULL_DATABASE_URL": legacy_database,
                "TEST_TEMPORAL_TARGET": temporal_target,
            },
        )
        assert candidate_for_conditional_cases["image_id"] == expected_image_id
        assert candidate_for_conditional_cases["database_url"] == database
        assert candidate_for_conditional_cases["legacy_null_database_url"] == legacy_database
        for collection in (
            "remote-publication-receipts",
            "job-checkpoints",
            "audit-events",
            "provenance-records",
        ):
            await test_rollback_restart_launcher_denies_omitted_collection_mutation_before_registration(
                collection
            )
        await test_rollback_restart_launcher_denies_legacy_null_v1_before_registration()
        await test_candidate_read_only_worker_creates_no_pyc_and_preserves_runtime_root()
        await test_rollback_restart_launcher_verifies_both_holds_before_exec()
        test_cross_container_adapter_recorder_positive_control()
        recorder_replay_evidence = []
        for reason in ("expired_permit_receipt", "published_during_cancel"):
            await assert_quiescent_runtime_boundary(reason)
            _restarted, reset_evidence = await reset_terminal_history_for_recorder(reason)
            primary_fixture["history_fixture"] = await capture_history_fixture(
                temporal_target, primary_operation_ids
            )
            await publish_progress()
            recorder_run_evidence = await test_cross_container_adapter_recorder_observes_zero_operations_for_both_holds(
                reason
            )
            await assert_quiescent_runtime_boundary(reason)
            recorder_replay_evidence.append(
                {"reset": reset_evidence, "recorder_run": recorder_run_evidence}
            )
            base_evidence["task7_recorder_replay_runs"] = recorder_replay_evidence
            save_json(progress_path, base_evidence)
        primary_fixture["history_fixture"] = await capture_history_fixture(
            temporal_target, primary_operation_ids
        )
        await publish_progress()

    primary_fixture["history_fixture"] = await capture_history_fixture(
        temporal_target, primary_operation_ids
    )
    await _publish_task8_progress_and_run_cases(
        progress_path,
        publish_progress=publish_progress,
        run_conditional_cases=run_conditional_cases,
        persist_progress=persist_fixtures,
    )

    # The exact control bundle denies every candidate descriptor because the
    # lock bytes no longer match its deployment-owned digest. A separately
    # supplied matching digest is mounted as an untrusted file but has no CLI
    # selection path and cannot replace the fixed trust root.
    rev_main = subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    unrelated_ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ANCHOR, rev_main],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert unrelated_ancestry.returncode == 1, "origin/main unexpectedly descends from the eligible anchor"
    unpinned_candidate = "e7a24a38ef920da3af071a51a288b859db9aaef1"
    unpinned_ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ANCHOR, unpinned_candidate],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert unpinned_ancestry.returncode == 0

    def run_denial(name, candidate, *, candidate_trust_root=False, extra_mounts=(), argv=None):
        lock_path, candidate_digest_path = save_candidate_lock(name, candidate)
        result = worker_harness.invoke_launcher(
            image=expected_image_id,
            database_url=database,
            control_bundle=control_bundle,
            candidate_lock=lock_path,
            independent_digest=candidate_digest_path if candidate_trust_root else None,
            extra_mounts=extra_mounts,
            argv=argv or (
                "--mode",
                "rollback-restart",
                "--worker-kind",
                "legacy-fixture",
                "--workspace-id",
                str(workspace_id),
                "--expired-hold-operation-id",
                str(expired_operation_id),
                "--cancel-hold-operation-id",
                str(cancelled_operation_id),
            ),
        )
        return result, lock_path, candidate_digest_path

    descriptors = {
        "a4": A4,
        "unknown": "f" * 40,
        "unpinned_same_history_descendant": unpinned_candidate,
        "unrelated_history": rev_main,
        "pre_floor": PREFLOOR,
    }
    descriptor_ancestry = {}
    for name, descriptor in descriptors.items():
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ANCHOR, descriptor],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        descriptor_ancestry[name] = (
            True if result.returncode == 0 else False if result.returncode == 1 else None
        )
    for name, descriptor in descriptors.items():
        candidate = json.loads(tracked_lock_bytes)
        candidate["pin"]["application_commit"] = descriptor
        result, candidate_lock_path, candidate_digest_path = run_denial(name, candidate)
        error_text = safe_text(result.stdout + result.stderr)
        assert result.returncode == 78, f"{name} candidate was not denied: {error_text}"
        assert "independent trust root" in error_text
        base_evidence["denials"][name] = {
            "application_commit": descriptor,
            "anchor_ancestor": descriptor_ancestry[name],
            "exit_code": result.returncode,
            "diagnostic": error_text.strip(),
            "candidate_lock_sha256": hashlib.sha256(candidate_lock_path.read_bytes()).hexdigest(),
            "candidate_digest_sha256": candidate_digest_path.read_text(encoding="ascii").strip(),
            "independent_digest_unchanged": (control_bundle / "publication-worker-launch-lock.sha256").read_text(encoding="ascii").strip() == trusted_digest,
            "worker_exec_reached": False,
        }

    for name in ("a4", "pre_floor"):
        candidate = json.loads(tracked_lock_bytes)
        candidate["pin"]["application_commit"] = descriptors[name]
        result, candidate_lock_path, candidate_digest_path = run_denial(
            f"{name}-authorized-test-root",
            candidate,
            candidate_trust_root=True,
        )
        error_text = safe_text(result.stdout + result.stderr)
        assert result.returncode == 78
        assert "application commit is prohibited" in error_text
        base_evidence["denials"][f"{name}_authenticated_candidate"] = {
            "application_commit": descriptors[name],
            "test_root_sha256": candidate_digest_path.read_text(encoding="ascii").strip(),
            "exit_code": result.returncode,
            "diagnostic": error_text.strip(),
            "worker_exec_reached": False,
        }

    self_generated = json.loads(tracked_lock_bytes)
    self_generated["pin"]["application_commit"] = unpinned_candidate
    self_lock_path, self_digest_path = save_candidate_lock("self-generated", self_generated)
    self_digest_mount = (self_digest_path, "/tmp/candidate-publication-worker-lock.sha256")
    self_result = worker_harness.invoke_launcher(
        image=expected_image_id,
        database_url=database,
        control_bundle=control_bundle,
        candidate_lock=self_lock_path,
        extra_mounts=(self_digest_mount,),
        argv=(
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "legacy-fixture",
            "--workspace-id",
            str(workspace_id),
            "--expired-hold-operation-id",
            str(expired_operation_id),
            "--cancel-hold-operation-id",
            str(cancelled_operation_id),
        ),
    )
    self_error = safe_text(self_result.stdout + self_result.stderr)
    assert self_result.returncode == 78
    assert "independent trust root" in self_error
    base_evidence["denials"]["self_generated_lock_and_untrusted_matching_digest"] = {
        "candidate_lock_sha256": hashlib.sha256(self_lock_path.read_bytes()).hexdigest(),
        "candidate_supplied_digest": self_digest_path.read_text(encoding="ascii").strip(),
        "candidate_supplied_digest_mount": "/tmp/candidate-publication-worker-lock.sha256",
        "fixed_independent_digest": trusted_digest,
        "fixed_independent_digest_unchanged": (control_bundle / "publication-worker-launch-lock.sha256").read_text(encoding="ascii").strip() == trusted_digest,
        "exit_code": self_result.returncode,
        "diagnostic": self_error.strip(),
        "worker_exec_reached": False,
    }

    # Independently authorized test locks get as far as the closed path checks,
    # which proves the real retained launcher rejects A4's executable, runtime,
    # and observed module paths before it can exec a worker.
    a4_checkout = Path("/tmp/salience-f3-retained-039ddf39cb9b846c205f352e083293fbec6f9fc5")
    a4_src = a4_checkout / "src"
    a4_checkout_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=a4_checkout,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    a4_checkout_status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=a4_checkout,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout
    assert a4_checkout_head == PREFLOOR
    assert a4_checkout_status == ""
    a4_source_pin_path = "src/salience/cycles/legacy_publication_runtime.py"
    a4_module = a4_src / "salience/cycles/legacy_publication_runtime.py"
    assert a4_module.is_file()
    a4_source_pin = next(
        item for item in tracked_lock["pin"]["source_files"]
        if item["path"] == a4_source_pin_path
    )
    a4_runtime_import_pin = next(
        item for item in tracked_lock["pin"]["runtime_imports"]
        if item["module"] == "salience.cycles.legacy_publication_runtime"
    )
    a4_module_bytes = a4_module.read_bytes()
    assert hashlib.sha256(a4_module_bytes).hexdigest() != a4_source_pin["sha256"]
    assert _git_blob(a4_module_bytes) != a4_source_pin["git_blob"]
    assert a4_runtime_import_pin["wheel_path"] == "salience/cycles/legacy_publication_runtime.py"
    assert a4_runtime_import_pin["sha256"] == a4_source_pin["sha256"]
    extracted_runtime = (
        Path("/tmp/p1-publication-recovery-0043/runtime-prefix")
        / tracked_lock["runtime_root"].lstrip("/")
    )
    record_path = Path(tracked_lock["pin"]["distribution_record_path"])
    record_relative_path = record_path.relative_to(Path(tracked_lock["runtime_root"]))
    record_file = extracted_runtime / record_relative_path
    record_bytes = record_file.read_bytes()
    assert hashlib.sha256(record_bytes).hexdigest() == tracked_lock["pin"][
        "distribution_record_sha256"
    ]
    with record_file.open(newline="", encoding="utf-8") as record_stream:
        record_entries = {row[0]: row for row in csv.reader(record_stream)}
    a4_record_entry = record_entries[a4_runtime_import_pin["wheel_path"]]
    installed_module = extracted_runtime / a4_runtime_import_pin["wheel_path"]
    installed_module_bytes = installed_module.read_bytes()
    assert a4_record_entry[1] == "sha256=" + _record_digest(installed_module_bytes)
    assert a4_record_entry[2] == str(len(installed_module_bytes))
    a4_interpreter = Path("/home/admin/Salience/.venv/bin/python")
    assert a4_interpreter.is_file()
    a4_overlay = tmp_path / "a4-runtime-overlay"
    shutil.copytree(a4_src, a4_overlay)
    shutil.copyfile(
        ROOT / "src/salience/cycles/publication_recovery_compatibility.py",
        a4_overlay / "salience/cycles/publication_recovery_compatibility.py",
    )

    a4_interpreter_lock = json.loads(tracked_lock_bytes)
    a4_interpreter_lock["interpreter_path"] = str(a4_interpreter)
    a4_interpreter_lock["interpreter_sha256"] = hashlib.sha256(a4_interpreter.read_bytes()).hexdigest()
    result, candidate_lock_path, candidate_digest_path = run_denial(
        "a4-interpreter",
        a4_interpreter_lock,
        candidate_trust_root=True,
    )
    error_text = safe_text(result.stdout + result.stderr)
    assert result.returncode == 78
    assert "interpreter path is not fixed" in error_text
    base_evidence["denials"]["a4_interpreter"] = {
        "path": str(a4_interpreter),
        "sha256": a4_interpreter_lock["interpreter_sha256"],
        "exit_code": result.returncode,
        "diagnostic": error_text.strip(),
        "worker_exec_reached": False,
    }

    a4_runtime_lock = json.loads(tracked_lock_bytes)
    assert a4_runtime_lock["runtime_root"] == attestation["runtime_root"]
    a4_runtime_target = str(
        Path(a4_runtime_lock["runtime_root"])
        / a4_source_pin_path.removeprefix("src/")
    )
    result, candidate_lock_path, candidate_digest_path = run_denial(
        "a4-runtime-source",
        a4_runtime_lock,
        candidate_trust_root=True,
        extra_mounts=((a4_module, a4_runtime_target),),
    )
    error_text = safe_text(result.stdout + result.stderr)
    assert result.returncode == 78
    assert "publication worker source pin mismatch" in error_text
    base_evidence["denials"]["a4_runtime_source"] = {
        "a4_source_commit": a4_checkout_head,
        "a4_checkout_clean": True,
        "source_pin_path": a4_source_pin_path,
        "a4_source_sha256": hashlib.sha256(a4_module_bytes).hexdigest(),
        "a4_source_git_blob": _git_blob(a4_module_bytes),
        "candidate_source_sha256": a4_source_pin["sha256"],
        "candidate_source_git_blob": a4_source_pin["git_blob"],
        "source_path": str(a4_module),
        "in_container_path": a4_runtime_target,
        "runtime_root": a4_runtime_lock["runtime_root"],
        "exit_code": result.returncode,
        "diagnostic": error_text.strip(),
        "source_pin_check_reached": True,
        "worker_import_or_registration_reached": False,
        "worker_exec_reached": False,
    }

    a4_module_lock = json.loads(tracked_lock_bytes)
    publication_import = next(
        item for item in a4_module_lock["pin"]["runtime_imports"]
        if item["module"] == "salience.workflows.publication"
    )
    publication_import["observed_path"] = "/app/salience/workflows/publication.py"
    result, candidate_lock_path, candidate_digest_path = run_denial(
        "a4-module-path",
        a4_module_lock,
        candidate_trust_root=True,
        extra_mounts=((a4_overlay, "/app"),),
    )
    error_text = safe_text(result.stdout + result.stderr)
    assert result.returncode == 78
    assert "publication worker import path does not match its RECORD pin" in error_text
    base_evidence["denials"]["a4_module_path"] = {
        "module": "salience.workflows.publication",
        "observed_path": "/app/salience/workflows/publication.py",
        "exit_code": result.returncode,
        "diagnostic": error_text.strip(),
        "worker_exec_reached": False,
    }

    wrong_workspace = uuid4()
    cross_workspace = worker_harness.invoke_launcher(
        image=expected_image_id,
        database_url=database,
        control_bundle=control_bundle,
        argv=(
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "legacy-fixture",
            "--workspace-id",
            str(wrong_workspace),
            "--expired-hold-operation-id",
            str(expired_operation_id),
            "--cancel-hold-operation-id",
            str(cancelled_operation_id),
        ),
    )
    cross_error = safe_text(cross_workspace.stdout + cross_workspace.stderr)
    assert cross_workspace.returncode == 78
    assert "both retained publication recovery holds failed verification" in cross_error
    base_evidence["denials"]["cross_workspace"] = {
        "workspace_id": str(wrong_workspace),
        "operation_ids": {key: str(value) for key, value in operation_ids.items()},
        "exit_code": cross_workspace.returncode,
        "diagnostic": cross_error.strip(),
        "worker_exec_reached": False,
    }

    base_evidence["liability_snapshot_hashes_after"] = {
        reason: canonical_hash(_liability_snapshot(database, operation_id))
        for reason, operation_id in operation_ids.items()
    }
    assert base_evidence["liability_snapshot_hashes_after"] == base_evidence[
        "liability_snapshot_hashes"
    ]
    third_views = verify_publication_recovery_rollback(
        database, workspace_id=workspace_id, operation_ids=operation_ids
    )
    assert third_views == second_views
    third_verifier = {
        "workspace_id": str(workspace_id),
        "reasons": ["expired_permit_receipt", "published_during_cancel"],
        "operation_ids": {key: str(value) for key, value in operation_ids.items()},
        "hold_ids": {
            "expired_permit_receipt": str(third_views[0].id),
            "published_during_cancel": str(third_views[1].id),
        },
        "verified": True,
        "exit_code": 0,
    }
    assert third_verifier == second_verifier
    base_evidence["third_verifier"] = third_verifier

    save_json(final_evidence_path, base_evidence)
