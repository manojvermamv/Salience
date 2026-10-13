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
import subprocess
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


def _lock_bytes(lock) -> bytes:
    return (json.dumps(asdict(lock), sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_test_lock(tmp_path, monkeypatch, *, include_interpreter_prefix_record=False):
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
    monkeypatch.setattr(
        launcher,
        "_interpreter_installation_prefix",
        lambda: Path(lock.runtime_root).parent.resolve(),
    )
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


def _synthetic_builder_inputs(tmp_path):
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
            [lock.interpreter_path, "-I", "-m", "salience.workflows.worker"],
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
    active_0042_database, monkeypatch, tmp_path,
):
    qualification_worker = ROOT / "tests/fixtures/v4_publication_floor_worker.py"
    assert qualification_worker.is_file(), "actual pinned-image fixture harness is required"

    import importlib.util
    import shutil
    import subprocess
    from uuid import uuid4

    import psycopg
    from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError

    from test_v4_legacy_publication import (
        _allow,
        _exception_chain,
        _hard_exit_publication_worker,
        _publication_fixture,
        _recovery_projection_counts,
        _wait_for_publication_permit_expiry,
    )
    from test_v4_publication_recovery_holds import (
        _assert_named_liabilities_unchanged,
        _liability_snapshot,
        _recovery_hold_count,
        _recovery_hold_snapshot,
    )
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.publication_recovery import (
        record_publication_recovery_hold,
        verify_publication_recovery_rollback,
    )
    from salience.cycles.runtime import TemporalCycleTransport

    database = active_0042_database
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    monkeypatch.setenv("TEST_DATABASE_URL", database)

    spec = importlib.util.spec_from_file_location(
        "v4_publication_floor_worker", qualification_worker
    )
    assert spec is not None and spec.loader is not None
    worker_harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker_harness)

    evidence_root = ROOT / "artifacts/v4-next-p1/agents/task-10-floor-qualification"
    tracked_lock_path = ROOT / "deployment/v4/publication-worker-launch-lock.json"
    tracked_lock_bytes = tracked_lock_path.read_bytes()
    tracked_lock = json.loads(tracked_lock_bytes)
    application_commit = tracked_lock["pin"]["application_commit"]
    candidate_suffix = application_commit[:7]
    candidate_root = evidence_root / f"candidate-{candidate_suffix}"
    control_bundle = candidate_root / "control-bundle"
    attestation = json.loads(
        (candidate_root / "image-attestation.json").read_text(encoding="utf-8")
    )
    image = attestation["image_ref"]
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
    assert image == f"salience-p1-floor-{candidate_suffix}-worker:latest"
    assert tracked_lock["pin"]["artifact_sha256"] == attestation["wheel_sha256"]
    assert tracked_lock["pin"]["distribution_record_sha256"] == attestation["record_sha256"]
    assert attestation["record_ok"] is True
    assert attestation["pyc_files"] == []
    assert attestation["source_tree_loaded"] is False

    def canonical_bytes(value):
        return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()

    def canonical_hash(value):
        return hashlib.sha256(canonical_bytes(value)).hexdigest()

    def save_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")

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
        "image_ref": image,
        "image_id": expected_image_id,
        "wheel_sha256": attestation["wheel_sha256"],
        "wheel_path": attestation["wheel_path"],
        "record_path": attestation["record_path"],
        "record_sha256": attestation["record_sha256"],
        "record_entry_count": attestation["record_entry_count"],
        "record_ok": attestation["record_ok"],
        "pyc_files": attestation["pyc_files"],
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
        "liability_snapshot_hashes": {},
        "hold_row_hashes": {},
        "projection_counts_before": {},
        "projection_counts_after": {},
        "worker_runs": [],
        "denials": {},
        "effects_enabled": False,
        "production_effects_enabled": False,
        "fixture_publication_budget": 0,
        "known_running_image_workers_before_restart": None,
    }
    progress_path = candidate_root / "worker-qualification-progress.json"
    final_evidence_path = candidate_root / "worker-qualification.json"
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
                    return
                description = await handle.describe()
                assert description.status == WorkflowExecutionStatus.RUNNING
                await asyncio.sleep(0.05)

    cancel_precommit_path = tmp_path / "cancel-hold-precommit-liabilities.json"
    cancel_exit = await _hard_exit_publication_worker(
        cancelled_fixture,
        tmp_path,
        after_acceptance=False,
        after_hold_commit=True,
        precommit_snapshot_path=cancel_precommit_path,
        operation_id=str(cancelled_operation_id),
        cancel_after_processing=True,
        cancel_ready_callback=close_after_processing_read,
    )
    assert cancel_exit == 74, (
        f"cancel crash-after-commit worker exited {cancel_exit}; "
        f"diagnostics={safe_text(cancel_precommit_path.read_text()) if cancel_precommit_path.exists() else 'missing'}"
    )
    assert cancel_precommit_path.is_file()
    cancel_before = json.loads(cancel_precommit_path.read_text(encoding="utf-8"))
    cancel_after_hold = _liability_snapshot(database, cancelled_operation_id)
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
    assert await _hard_exit_publication_worker(
        expired_fixture,
        tmp_path,
        after_acceptance=True,
        permit_ttl_seconds=5,
        operation_id=str(expired_operation_id),
    ) == 73
    await _wait_for_publication_permit_expiry(database, str(expired_operation_id))
    expired_before_hold = _liability_snapshot(database, expired_operation_id)
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

    operation_ids = {
        "expired_permit_receipt": expired_operation_id,
        "published_during_cancel": cancelled_operation_id,
    }
    expected_hold_ids = {
        "expired_permit_receipt": str(expired_hold.id),
        "published_during_cancel": cancelled_hold_row["id"],
    }
    first_views = verify_publication_recovery_rollback(
        database, workspace_id=workspace_id, operation_ids=operation_ids
    )
    first_verifier = {
        "workspace_id": str(workspace_id),
        "reasons": ["expired_permit_receipt", "published_during_cancel"],
        "operation_ids": {key: str(value) for key, value in operation_ids.items()},
        "hold_ids": {"expired_permit_receipt": str(first_views[0].id), "published_during_cancel": str(first_views[1].id)},
        "verified": True,
    }
    assert first_verifier["hold_ids"] == expected_hold_ids

    before_snapshots = {
        "expired_permit_receipt": expired_after_hold,
        "published_during_cancel": cancel_after_hold,
    }
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
        running = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={image}", "--format", "{{.ID}} {{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
            cwd=tmp_path,
            env=worker_harness._docker_environment(),
            timeout=15,
        ).stdout.splitlines()
    assert running == [], running
    base_evidence["known_running_image_workers_before_restart"] = running

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    worker_cases = (
        ("expired_permit_receipt", expired_fixture["queue"], expired_operation_id),
        ("published_during_cancel", cancelled_fixture["queue"], cancelled_operation_id),
    )
    for reason, task_queue, operation_id in worker_cases:
        workflow_id = "salience-v4-legacy-operation:" + str(operation_id)
        handle = client.get_workflow_handle(workflow_id)
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        history_before_restart = await handle.fetch_history()
        event_id_before_restart = max(
            (event.event_id for event in history_before_restart.events), default=0
        )
        name = "publication-floor-" + reason.replace("_", "-") + "-" + uuid4().hex[:10]
        launch = worker_harness.start_rollback_worker(
            image=image,
            database_url=database,
            temporal_target=os.environ["TEST_TEMPORAL_TARGET"],
            workspace_id=str(workspace_id),
            expired_operation_id=str(expired_operation_id),
            cancel_operation_id=str(cancelled_operation_id),
            task_queue=task_queue,
            control_bundle=control_bundle,
            name=name,
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
            assert inspected["args"][:3] == [
                "/run/salience-control/launch-v4-publication-worker.py",
                "--mode",
                "rollback-restart",
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
            run_evidence["stop"] = {
                "state": stopped.get("state"),
                "logs_path": str(logs_path.relative_to(evidence_root)),
                "logs_exit_code": stopped.get("logs_exit_code"),
            }
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
        base_evidence["worker_runs"].append(run_evidence)
        save_json(progress_path, base_evidence)

    after_snapshots = {
        "expired_permit_receipt": _liability_snapshot(database, expired_operation_id),
        "published_during_cancel": _liability_snapshot(database, cancelled_operation_id),
    }
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
            "provider_submit_calls": 0,
            "provider_status_calls": 0,
            "adapter_counter_availability": {
                "submit_count": "process-local; not exported by the fixture worker",
                "status_count": "not exposed by the fixture adapter",
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
            "basis": "The pinned image history shows only the reason-specific guarded activity; its logged typed-hold branch exits before provider submit/status paths, and the fixture progress plus full liabilities are unchanged.",
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
            image=image,
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
        image=image,
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
    a4_module = a4_src / "salience/workflows/publication.py"
    assert a4_module.is_file()
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
    a4_runtime_lock["runtime_root"] = "/app"
    result, candidate_lock_path, candidate_digest_path = run_denial(
        "a4-runtime-root",
        a4_runtime_lock,
        candidate_trust_root=True,
        extra_mounts=((a4_overlay, "/app"),),
    )
    error_text = safe_text(result.stdout + result.stderr)
    assert result.returncode == 78
    assert "publication worker source pin mismatch" in error_text
    base_evidence["denials"]["a4_runtime_root"] = {
        "path": "/app",
        "exit_code": result.returncode,
        "diagnostic": error_text.strip(),
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
        image=image,
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
    assert verify_publication_recovery_rollback(
        database, workspace_id=workspace_id, operation_ids=operation_ids
    ) == second_views
    save_json(final_evidence_path, base_evidence)
    save_json(progress_path, base_evidence)
