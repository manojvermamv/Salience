"""Fail-closed compatibility gate for publication-worker starts.

The gate is deliberately read-only.  It is not a release switch and it never
changes a retained hold or its underlying publication liability.
"""

from __future__ import annotations

import ast
import base64
import csv
from dataclasses import asdict, dataclass
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Literal, Mapping
from uuid import UUID

import psycopg


PublicationWorkerStartMode = Literal["current", "rollback-restart"]
PublicationWorkerKind = Literal["deployable", "legacy-fixture"]

ELIGIBLE_PUBLICATION_WORKER_ANCHOR = "69d7ab5da02cc9b8013f17b3e9aed3f7edb758a6"
PROHIBITED_PUBLICATION_WORKER_COMMITS = frozenset(
    {
        "039ddf39cb9b846c205f352e083293fbec6f9fc5",
        "a4b2368cdb463939d093020224a5b5667392aee6",
    }
)
RETAINED_PUBLICATION_WHEEL = "/opt/salience-artifacts/salience.whl"
PUBLICATION_WORKER_LOCK_PATH = "/run/salience-control/publication-worker-launch-lock.json"
PUBLICATION_WORKER_LOCK_DIGEST_PATH = "/run/salience-control/publication-worker-launch-lock.sha256"
PUBLICATION_WORKER_LAUNCHER_PATH = "/run/salience-control/launch-v4-publication-worker.py"
PUBLICATION_WORKER_START_MODE_ENV = "SALIENCE_PUBLICATION_WORKER_START_MODE"
PUBLICATION_WORKER_LAUNCH_LOCK_ENV = "SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK"
PUBLICATION_WORKER_LAUNCH_LOCK_SHA256_ENV = "SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256"
PUBLICATION_ROLLBACK_WORKSPACE_ENV = "SALIENCE_PUBLICATION_ROLLBACK_WORKSPACE_ID"
PUBLICATION_EXPIRED_HOLD_OPERATION_ENV = "SALIENCE_PUBLICATION_EXPIRED_HOLD_OPERATION_ID"
PUBLICATION_CANCEL_HOLD_OPERATION_ENV = "SALIENCE_PUBLICATION_CANCEL_HOLD_OPERATION_ID"

_REQUIRED_SOURCE_PATHS = frozenset(
    {
        "alembic.ini",
        "migrations/__init__.py",
        "migrations/env.py",
        "migrations/versions/__init__.py",
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
        "migrations/versions/0043_complete_publication_recovery_liabilities.py",
    }
)
_REQUIRED_MIGRATION_SUPPORT_PATHS = frozenset(
    {
        "alembic.ini",
        "migrations/__init__.py",
        "migrations/env.py",
        "migrations/versions/__init__.py",
    }
)
_MIGRATION_VERSIONS_PREFIX = "migrations/versions/"
_MIGRATION_FLOOR_REVISION = "0043_complete_publication_recovery_liabilities"
_MIGRATION_PREVIOUS_REVISION = "0042_publication_recovery_holds"
_REQUIRED_IMPORT_MODULES = frozenset(
    {
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
)
_RECOVERY_REASON_ORDER = ("expired_permit_receipt", "published_during_cancel")
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class PublicationWorkerCompatibilityError(RuntimeError):
    """A publication worker cannot prove compatibility with retained holds."""


@dataclass(frozen=True)
class PublicationWorkerSourcePin:
    path: str
    git_blob: str
    sha256: str


@dataclass(frozen=True)
class PublicationWorkerImportPin:
    module: str
    wheel_path: str
    observed_path: str
    sha256: str


@dataclass(frozen=True)
class PublicationWorkerPin:
    schema_version: Literal["salience.publication-worker-pin.v1"]
    application_commit: str
    eligible_anchor: str
    artifact_path: str
    artifact_sha256: str
    distribution_record_path: str
    distribution_record_sha256: str
    source_manifest_sha256: str
    source_files: tuple[PublicationWorkerSourcePin, ...]
    runtime_imports: tuple[PublicationWorkerImportPin, ...]


@dataclass(frozen=True)
class PublicationWorkerEntrypoint:
    module: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class PublicationWorkerLaunchLock:
    schema_version: Literal["salience.publication-worker-launch-lock.v1"]
    launcher_sha256: str
    interpreter_path: str
    interpreter_sha256: str
    runtime_root: str
    workers: Mapping[PublicationWorkerKind, PublicationWorkerEntrypoint]
    pin: PublicationWorkerPin


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _canonical_lock_bytes(launch_lock: PublicationWorkerLaunchLock) -> bytes:
    return _canonical_json(asdict(launch_lock))


def _source_manifest_bytes(source_files: tuple[PublicationWorkerSourcePin, ...]) -> bytes:
    return _canonical_json([asdict(item) for item in source_files])


def _sha256_file(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError as error:
        raise PublicationWorkerCompatibilityError(
            f"required pinned file is unavailable: {path}"
        ) from error


def _artifact_file_path(_pin: PublicationWorkerPin) -> Path:
    return Path(RETAINED_PUBLICATION_WHEEL)


def _launcher_file_path() -> Path:
    return Path(PUBLICATION_WORKER_LAUNCHER_PATH)


def _source_file_path(path: str, runtime_root: Path) -> Path:
    if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
        raise PublicationWorkerCompatibilityError("source pin path is not repository-relative")
    relative = Path(path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or relative.as_posix() != path
    ):
        raise PublicationWorkerCompatibilityError("source pin path is not repository-relative")
    if path == "scripts/launch-v4-publication-worker.py":
        return _launcher_file_path()
    if path.startswith("src/"):
        relative = Path(*relative.parts[1:])
    return runtime_root / relative


def _observed_import_path(module: str) -> str:
    try:
        imported = importlib.import_module(module)
        observed = getattr(imported, "__file__", None)
    except Exception as error:
        raise PublicationWorkerCompatibilityError(
            f"pinned runtime import could not be loaded: {module}"
        ) from error
    if not isinstance(observed, str):
        raise PublicationWorkerCompatibilityError(
            f"pinned runtime import has no file location: {module}"
        )
    return observed


def _contained_file(path: Path, root: Path) -> Path:
    try:
        root = root.resolve(strict=True)
        candidate = path.absolute()
        relative = candidate.relative_to(root)
    except OSError as error:
        raise PublicationWorkerCompatibilityError(
            f"required pinned file is unavailable: {path}"
        ) from error
    except ValueError as error:
        raise PublicationWorkerCompatibilityError(
            f"pinned file escapes runtime root: {path}"
        ) from error
    current = root
    for component in relative.parts:
        current = current / component
        try:
            if current.is_symlink():
                raise PublicationWorkerCompatibilityError(
                    f"pinned file uses a symlink path: {path}"
                )
        except OSError as error:
            raise PublicationWorkerCompatibilityError(
                f"required pinned file is unavailable: {path}"
            ) from error
    try:
        file_stat = current.lstat()
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise PublicationWorkerCompatibilityError(
            f"required pinned file is unavailable: {path}"
        ) from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PublicationWorkerCompatibilityError(
            f"pinned file escapes runtime root: {path}"
        ) from error
    if not stat.S_ISREG(file_stat.st_mode):
        raise PublicationWorkerCompatibilityError(f"pinned file is not regular: {path}")
    return resolved


def _source_file_bytes(path: str, runtime_root: Path) -> bytes:
    source_path = _source_file_path(path, runtime_root)
    if path != "scripts/launch-v4-publication-worker.py":
        source_path = _contained_file(source_path, runtime_root)
    else:
        try:
            if source_path.is_symlink() or not stat.S_ISREG(source_path.lstat().st_mode):
                raise PublicationWorkerCompatibilityError(
                    "pinned launcher source is not a regular file"
                )
        except OSError as error:
            raise PublicationWorkerCompatibilityError(
                f"required pinned file is unavailable: {source_path}"
            ) from error
    try:
        return source_path.read_bytes()
    except OSError as error:
        raise PublicationWorkerCompatibilityError(
            f"required pinned file is unavailable: {source_path}"
        ) from error


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _interpreter_installation_prefix(interpreter_path: str) -> Path:
    try:
        installation_prefix = Path(interpreter_path).absolute().parent.parent.resolve(strict=True)
    except OSError as error:
        raise PublicationWorkerCompatibilityError(
            "fixed interpreter installation prefix is unavailable"
        ) from error
    if not installation_prefix.is_dir():
        raise PublicationWorkerCompatibilityError(
            "fixed interpreter installation prefix is not a directory"
        )
    return installation_prefix


def _validate_record(lock: PublicationWorkerLaunchLock, runtime_root: Path) -> set[str]:
    record_path = _contained_file(Path(lock.pin.distribution_record_path), runtime_root)
    if _sha256_file(record_path) != lock.pin.distribution_record_sha256:
        raise PublicationWorkerCompatibilityError("installed distribution RECORD digest mismatch")
    installation_prefix = _interpreter_installation_prefix(lock.interpreter_path)
    recorded: set[str] = set()
    normalized_targets: set[str] = set()
    try:
        rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    except (OSError, UnicodeError, csv.Error) as error:
        raise PublicationWorkerCompatibilityError("installed distribution RECORD is invalid") from error
    if not rows:
        raise PublicationWorkerCompatibilityError("installed distribution RECORD is empty")
    normalized_root = runtime_root.resolve(strict=True)
    record_relative = str(record_path.relative_to(normalized_root))
    for row in rows:
        if len(row) != 3:
            raise PublicationWorkerCompatibilityError("installed distribution RECORD row is malformed")
        relative, encoded_digest, size = row
        candidate = Path(relative)
        if candidate.is_absolute() or not relative:
            raise PublicationWorkerCompatibilityError("installed distribution RECORD path is unsafe")
        if relative in recorded:
            raise PublicationWorkerCompatibilityError("installed distribution RECORD repeats a path")
        recorded.add(relative)
        is_record_self = relative == record_relative
        if is_record_self:
            if encoded_digest or size:
                raise PublicationWorkerCompatibilityError(
                    "installed distribution RECORD self entry must be unhashed"
                )
            file_path = record_path
        else:
            if (
                not encoded_digest.startswith("sha256=")
                or not size.isascii()
                or not size.isdecimal()
            ):
                raise PublicationWorkerCompatibilityError(
                    "installed distribution RECORD file hash or size is missing"
                )
            try:
                file_path = (runtime_root / candidate).resolve(strict=True)
            except OSError as error:
                raise PublicationWorkerCompatibilityError(
                    f"installed distribution RECORD file is unavailable: {relative}"
                ) from error
            try:
                file_path.relative_to(installation_prefix)
            except ValueError as error:
                raise PublicationWorkerCompatibilityError(
                    f"installed distribution RECORD path escapes interpreter prefix: {relative}"
                ) from error
            if not file_path.is_file():
                raise PublicationWorkerCompatibilityError(
                    f"installed distribution RECORD path is not a file: {relative}"
                )
            if file_path == record_path:
                raise PublicationWorkerCompatibilityError(
                    "installed distribution RECORD path aliases its self entry"
                )
        normalized_target = str(file_path)
        if normalized_target in normalized_targets:
            raise PublicationWorkerCompatibilityError(
                "installed distribution RECORD repeats a normalized target"
            )
        normalized_targets.add(normalized_target)
        content = file_path.read_bytes()
        if is_record_self:
            continue
        actual = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        if encoded_digest[7:] != actual or size != str(len(content)):
            raise PublicationWorkerCompatibilityError(
                f"installed distribution RECORD file mismatch: {relative}"
            )
    if record_relative not in recorded:
        raise PublicationWorkerCompatibilityError("installed distribution RECORD omits itself")
    return recorded


def _validate_worker_entrypoints(lock: PublicationWorkerLaunchLock) -> None:
    expected = {
        "deployable": PublicationWorkerEntrypoint("salience.workflows.worker", ()),
        "legacy-fixture": PublicationWorkerEntrypoint("salience.cycles.legacy_runtime", ()),
    }
    if dict(lock.workers) != expected:
        raise PublicationWorkerCompatibilityError("publication worker entrypoint is not a closed pin")


def _validate_lock_and_pin(
    launch_lock: PublicationWorkerLaunchLock,
    observed_launch_lock_sha256: str,
) -> None:
    if launch_lock.schema_version != "salience.publication-worker-launch-lock.v1":
        raise PublicationWorkerCompatibilityError("publication worker launch-lock schema is unsupported")
    lock_digest = hashlib.sha256(_canonical_lock_bytes(launch_lock)).hexdigest()
    if not _HEX_64.fullmatch(observed_launch_lock_sha256) or lock_digest != observed_launch_lock_sha256:
        raise PublicationWorkerCompatibilityError("publication worker launch-lock trust digest mismatch")

    if launch_lock.interpreter_path != sys.executable:
        try:
            if not Path(launch_lock.interpreter_path).samefile(sys.executable):
                raise PublicationWorkerCompatibilityError("publication worker interpreter path mismatch")
        except OSError as error:
            raise PublicationWorkerCompatibilityError("publication worker interpreter path is unavailable") from error
    if not _HEX_64.fullmatch(launch_lock.interpreter_sha256):
        raise PublicationWorkerCompatibilityError("publication worker interpreter hash is malformed")
    if _sha256_file(Path(launch_lock.interpreter_path)) != launch_lock.interpreter_sha256:
        raise PublicationWorkerCompatibilityError("publication worker interpreter hash mismatch")

    if not _HEX_64.fullmatch(launch_lock.launcher_sha256):
        raise PublicationWorkerCompatibilityError("publication worker launcher hash is malformed")
    if _sha256_file(_launcher_file_path()) != launch_lock.launcher_sha256:
        raise PublicationWorkerCompatibilityError("publication worker launcher hash mismatch")

    pin = launch_lock.pin
    if pin.schema_version != "salience.publication-worker-pin.v1":
        raise PublicationWorkerCompatibilityError("publication worker pin schema is unsupported")
    if not _HEX_40.fullmatch(pin.application_commit):
        raise PublicationWorkerCompatibilityError("publication worker application commit is malformed")
    if pin.application_commit in PROHIBITED_PUBLICATION_WORKER_COMMITS:
        raise PublicationWorkerCompatibilityError("publication worker application commit is prohibited")
    if pin.eligible_anchor != ELIGIBLE_PUBLICATION_WORKER_ANCHOR:
        raise PublicationWorkerCompatibilityError("publication worker is not pinned to the eligible anchor")
    if pin.artifact_path != RETAINED_PUBLICATION_WHEEL:
        raise PublicationWorkerCompatibilityError("publication worker artifact path is not retained")
    if not _HEX_64.fullmatch(pin.artifact_sha256):
        raise PublicationWorkerCompatibilityError("publication worker artifact hash is malformed")
    if _sha256_file(_artifact_file_path(pin)) != pin.artifact_sha256:
        raise PublicationWorkerCompatibilityError("retained publication worker wheel hash mismatch")

    runtime_root = Path(launch_lock.runtime_root)
    if not runtime_root.is_absolute():
        raise PublicationWorkerCompatibilityError("publication worker runtime root must be absolute")
    try:
        runtime_root = runtime_root.resolve(strict=True)
    except OSError as error:
        raise PublicationWorkerCompatibilityError("publication worker runtime root is unavailable") from error
    if not runtime_root.is_dir():
        raise PublicationWorkerCompatibilityError("publication worker runtime root is not a directory")

    source_paths = [item.path for item in pin.source_files]
    if (
        any(not isinstance(path, str) for path in source_paths)
        or len(source_paths) != len(set(source_paths))
        or not _REQUIRED_SOURCE_PATHS <= set(source_paths)
    ):
        raise PublicationWorkerCompatibilityError("publication worker source manifest is incomplete")
    if not _HEX_64.fullmatch(pin.source_manifest_sha256):
        raise PublicationWorkerCompatibilityError("publication worker source manifest hash is malformed")
    if hashlib.sha256(_source_manifest_bytes(pin.source_files)).hexdigest() != pin.source_manifest_sha256:
        raise PublicationWorkerCompatibilityError("publication worker source manifest digest mismatch")
    verified_source_files: dict[str, bytes] = {}
    for source_pin in pin.source_files:
        if not isinstance(source_pin.path, str):
            raise PublicationWorkerCompatibilityError("publication worker source path is malformed")
        if not _HEX_40.fullmatch(source_pin.git_blob) or not _HEX_64.fullmatch(source_pin.sha256):
            raise PublicationWorkerCompatibilityError("publication worker source pin hash is malformed")
        content = _source_file_bytes(source_pin.path, runtime_root)
        if hashlib.sha256(content).hexdigest() != source_pin.sha256 or _git_blob_sha1(content) != source_pin.git_blob:
            raise PublicationWorkerCompatibilityError(f"publication worker source pin mismatch: {source_pin.path}")
        verified_source_files[source_pin.path] = content

    import_modules = [item.module for item in pin.runtime_imports]
    if len(import_modules) != len(set(import_modules)) or not _REQUIRED_IMPORT_MODULES <= set(import_modules):
        raise PublicationWorkerCompatibilityError("publication worker runtime import manifest is incomplete")
    recorded = _validate_record(launch_lock, runtime_root)
    for import_pin in pin.runtime_imports:
        relative = Path(import_pin.wheel_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise PublicationWorkerCompatibilityError("publication worker import path is unsafe")
        expected_module_path = import_pin.module.replace(".", "/") + ".py"
        if import_pin.wheel_path != expected_module_path:
            raise PublicationWorkerCompatibilityError("publication worker import path does not match module")
        expected_file = _contained_file(runtime_root / relative, runtime_root)
        if import_pin.wheel_path not in recorded:
            raise PublicationWorkerCompatibilityError("installed distribution RECORD omits a pinned import")
        observed_file = _contained_file(Path(_observed_import_path(import_pin.module)), runtime_root)
        pinned_observed_file = _contained_file(Path(import_pin.observed_path), runtime_root)
        if observed_file != expected_file or pinned_observed_file != expected_file:
            raise PublicationWorkerCompatibilityError(
                f"publication worker import resolved outside its exact pin: {import_pin.module}"
            )
        if _sha256_file(expected_file) != import_pin.sha256:
            raise PublicationWorkerCompatibilityError(
                f"publication worker import hash mismatch: {import_pin.module}"
            )
    _validate_worker_entrypoints(launch_lock)
    _validate_migration_inventory(launch_lock, verified_source_files)
    return verified_source_files


def _contained_directory(path: Path, root: Path) -> Path:
    try:
        root = root.resolve(strict=True)
        relative = path.absolute().relative_to(root)
    except OSError as error:
        raise PublicationWorkerCompatibilityError(
            f"required migration directory is unavailable: {path}"
        ) from error
    except ValueError as error:
        raise PublicationWorkerCompatibilityError(
            f"migration directory escapes runtime root: {path}"
        ) from error
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise PublicationWorkerCompatibilityError(
                f"migration directory uses a symlink path: {path}"
            )
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise PublicationWorkerCompatibilityError(
            f"required migration directory is unavailable: {path}"
        ) from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PublicationWorkerCompatibilityError(
            f"migration directory escapes runtime root: {path}"
        ) from error
    if not resolved.is_dir():
        raise PublicationWorkerCompatibilityError(
            f"migration path is not a directory: {path}"
        )
    return resolved


def _validate_migration_inventory(
    launch_lock: PublicationWorkerLaunchLock,
    verified_source_files: Mapping[str, bytes],
) -> tuple[str, ...]:
    pin = launch_lock.pin
    source_names = {item.path for item in pin.source_files}
    if not _REQUIRED_MIGRATION_SUPPORT_PATHS <= source_names:
        raise PublicationWorkerCompatibilityError(
            "authenticated Alembic configuration inventory is incomplete"
        )

    expected_migration_files = {
        name for name in source_names if name.startswith("migrations/")
    }
    runtime_root = Path(launch_lock.runtime_root).resolve(strict=True)
    migrations_root = _contained_directory(runtime_root / "migrations", runtime_root)
    actual_migration_files: set[str] = set()
    for directory, dir_names, file_names in os.walk(migrations_root, followlinks=False):
        directory_path = Path(directory)
        for name in tuple(dir_names):
            child = directory_path / name
            if child.is_symlink():
                raise PublicationWorkerCompatibilityError(
                    f"migration inventory uses a symlink path: {child}"
                )
            if name == "__pycache__":
                dir_names.remove(name)
        for name in file_names:
            child = directory_path / name
            if child.is_symlink():
                raise PublicationWorkerCompatibilityError(
                    f"migration inventory uses a symlink path: {child}"
                )
            if name.endswith(".pyc"):
                continue
            try:
                child_stat = child.lstat()
                child_relative = child.relative_to(runtime_root).as_posix()
            except OSError as error:
                raise PublicationWorkerCompatibilityError(
                    f"migration inventory file is unavailable: {child}"
                ) from error
            if not stat.S_ISREG(child_stat.st_mode):
                raise PublicationWorkerCompatibilityError(
                    f"migration inventory file is not regular: {child}"
                )
            actual_migration_files.add(child_relative)
    if actual_migration_files != expected_migration_files:
        raise PublicationWorkerCompatibilityError(
            "authenticated migration/config inventory does not match runtime files"
        )

    expected_revision_files = {
        name
        for name in expected_migration_files
        if (
            name.startswith(_MIGRATION_VERSIONS_PREFIX)
            and name.endswith(".py")
            and Path(name).name != "__init__.py"
        )
    }
    versions_root = _contained_directory(
        runtime_root / "migrations" / "versions", runtime_root
    )
    actual_revision_files: set[str] = set()
    for path in versions_root.iterdir():
        if not path.name.endswith(".py") or path.name == "__init__.py":
            continue
        if path.is_symlink():
            raise PublicationWorkerCompatibilityError(
                f"migration revision path is a symlink: {path}"
            )
        try:
            path_stat = path.lstat()
        except OSError as error:
            raise PublicationWorkerCompatibilityError(
                f"migration revision file is unavailable: {path}"
            ) from error
        if not stat.S_ISREG(path_stat.st_mode):
            raise PublicationWorkerCompatibilityError(
                f"migration revision path is not a regular file: {path}"
            )
        actual_revision_files.add(path.relative_to(runtime_root).as_posix())
    if actual_revision_files != expected_revision_files:
        raise PublicationWorkerCompatibilityError(
            "authenticated Alembic revision inventory does not match runtime files"
        )

    numbered_paths: dict[int, list[str]] = {}
    for name in expected_revision_files:
        filename = Path(name).name
        match = re.match(r"^(\d{4})_.+\.py$", filename)
        if match is None:
            raise PublicationWorkerCompatibilityError(
                f"authenticated migration revision filename is malformed: {name}"
            )
        numbered_paths.setdefault(int(match.group(1)), []).append(name)
    if any(len(numbered_paths.get(number, ())) != 1 for number in range(1, 44)):
        raise PublicationWorkerCompatibilityError(
            "authenticated migration inventory must include one revision from 0001 through 0043"
        )
    if not expected_revision_files <= set(verified_source_files):
        raise PublicationWorkerCompatibilityError(
            "authenticated migration source bytes are unavailable"
        )
    return tuple(sorted(expected_revision_files))


def _validate_migration_graph(
    revisions: Mapping[str, tuple[str, ...]],
) -> set[str]:
    """Require one complete lineage with the exact 0042-to-0043 edge."""
    if (
        not revisions
        or _MIGRATION_PREVIOUS_REVISION not in revisions
        or _MIGRATION_FLOOR_REVISION not in revisions
    ):
        raise PublicationWorkerCompatibilityError("trusted publication recovery migration is missing")
    if any(parent not in revisions for parents in revisions.values() for parent in parents):
        raise PublicationWorkerCompatibilityError("trusted Alembic migration graph has an unknown parent")
    if any(len(parents) > 1 for parents in revisions.values()):
        raise PublicationWorkerCompatibilityError("trusted Alembic migration graph is ambiguous")
    if revisions[_MIGRATION_FLOOR_REVISION] != (_MIGRATION_PREVIOUS_REVISION,):
        raise PublicationWorkerCompatibilityError(
            "trusted publication recovery migrations do not form the exact 0042-to-0043 chain"
        )

    states: dict[str, int] = {}

    def visit(revision: str) -> None:
        state = states.get(revision, 0)
        if state == 1:
            raise PublicationWorkerCompatibilityError(
                "trusted Alembic migration graph contains a cycle"
            )
        if state == 2:
            return
        states[revision] = 1
        for parent in revisions[revision]:
            visit(parent)
        states[revision] = 2

    for revision in revisions:
        visit(revision)

    referenced = {parent for parents in revisions.values() for parent in parents}
    roots = {revision for revision, parents in revisions.items() if not parents}
    heads = set(revisions) - referenced
    if len(roots) != 1 or len(heads) != 1:
        raise PublicationWorkerCompatibilityError("trusted Alembic migration graph is ambiguous")

    visited: set[str] = set()
    current = next(iter(heads))
    while True:
        if current in visited:
            raise PublicationWorkerCompatibilityError("trusted Alembic migration graph contains a cycle")
        visited.add(current)
        parents = revisions[current]
        if not parents:
            break
        current = parents[0]
    if visited != set(revisions):
        raise PublicationWorkerCompatibilityError("trusted Alembic migration graph is disconnected")
    return heads


def _safe_migration_expression(node: ast.AST, safe_names: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return node.value is None or isinstance(node.value, (str, bytes, int, float, bool))
    if isinstance(node, ast.Name):
        return isinstance(node.ctx, ast.Load) and node.id in safe_names
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_safe_migration_expression(item, safe_names) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None
            and _safe_migration_expression(key, safe_names)
            and _safe_migration_expression(value, safe_names)
            for key, value in zip(node.keys, node.values)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
        return _safe_migration_expression(node.left, safe_names) and _safe_migration_expression(
            node.right, safe_names
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _safe_migration_expression(node.operand, safe_names)
    if isinstance(node, ast.Subscript):
        return _safe_migration_expression(node.value, safe_names) and _safe_migration_expression(
            node.slice, safe_names
        )
    if isinstance(node, ast.Slice):
        return all(
            item is None or _safe_migration_expression(item, safe_names)
            for item in (node.lower, node.upper, node.step)
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and not node.keywords
        and _safe_migration_expression(node.func.value, safe_names)
        and all(isinstance(argument, ast.Constant) and isinstance(argument.value, str) for argument in node.args)
    ):
        return True
    return False


def _safe_migration_annotation(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value is None
    if isinstance(node, ast.Name):
        return node.id in {"str", "bytes", "int", "float", "bool", "list", "dict", "tuple", "set"}
    if isinstance(node, ast.Subscript):
        return _safe_migration_annotation(node.value) and _safe_migration_annotation(node.slice)
    if isinstance(node, ast.Tuple):
        return all(_safe_migration_annotation(item) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _safe_migration_annotation(node.left) and _safe_migration_annotation(node.right)
    if isinstance(node, ast.Slice):
        return all(
            item is None or _safe_migration_annotation(item)
            for item in (node.lower, node.upper, node.step)
        )
    return False


def _validate_migration_module_statements(tree: ast.Module) -> None:
    """Allow metadata declarations and normal migration definitions, not module behavior."""
    safe_names: set[str] = set()
    metadata_names = {"revision", "down_revision"}
    for index, statement in enumerate(tree.body):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            value = statement.value
            if (
                value is None
                or not targets
                or any(not isinstance(target, ast.Name) for target in targets)
            ):
                raise PublicationWorkerCompatibilityError(
                    "trusted Alembic migration has unsupported module-level assignment behavior"
                )
            target_names = {target.id for target in targets if isinstance(target, ast.Name)}
            if target_names & metadata_names:
                if not isinstance(statement, ast.Assign) or len(targets) != 1:
                    raise PublicationWorkerCompatibilityError(
                        "trusted Alembic revision metadata must use one simple literal assignment"
                    )
                continue
            if isinstance(statement, ast.AnnAssign) and not _safe_migration_annotation(statement.annotation):
                raise PublicationWorkerCompatibilityError(
                    "trusted Alembic migration has an unsafe module-level annotation"
                )
            if not _safe_migration_expression(value, safe_names):
                raise PublicationWorkerCompatibilityError(
                    "trusted Alembic migration has unsafe module-level assignment behavior"
                )
            safe_names.update(target_names)
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.decorator_list or statement.args.defaults or any(
                default is not None for default in statement.args.kw_defaults
            ):
                raise PublicationWorkerCompatibilityError(
                    "trusted Alembic migration has unsafe module-level function behavior"
                )
            annotations = [statement.returns]
            annotations.extend(
                argument.annotation
                for argument in (
                    *statement.args.posonlyargs,
                    *statement.args.args,
                    *statement.args.kwonlyargs,
                )
            )
            if any(
                annotation is not None and not _safe_migration_annotation(annotation)
                for annotation in annotations
            ):
                raise PublicationWorkerCompatibilityError(
                    "trusted Alembic migration has an unsafe module-level function annotation"
                )
            continue
        raise PublicationWorkerCompatibilityError(
            "trusted Alembic migration has unsupported module-level behavior"
        )


def _parse_revision_metadata(path: str, content: bytes) -> tuple[str, tuple[str, ...]]:
    """Read literal Alembic metadata from already authenticated source bytes."""
    try:
        text = content.decode("utf-8")
        tree = ast.parse(text, filename=path)
    except (SyntaxError, UnicodeError) as error:
        raise PublicationWorkerCompatibilityError(
            "trusted Alembic migration graph is invalid"
        ) from error

    _validate_migration_module_statements(tree)
    expressions: dict[str, ast.expr] = {}
    approved_targets: set[int] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in {"revision", "down_revision"}:
            continue
        name = target.id
        if value is None or name in expressions:
            raise PublicationWorkerCompatibilityError(
                "trusted Alembic revision metadata is ambiguous"
            )
        expressions[name] = value
        approved_targets.add(id(target))

    def assigned_binding_names(node: ast.AST) -> tuple[str, ...]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return (node.name,)
        if isinstance(node, ast.alias):
            return (node.asname or node.name.partition(".")[0],)
        if isinstance(node, ast.ExceptHandler) and node.name:
            return (node.name,)
        if isinstance(node, ast.MatchAs) and node.name:
            return (node.name,)
        if isinstance(node, ast.MatchStar) and node.name:
            return (node.name,)
        if isinstance(node, ast.MatchMapping) and node.rest:
            return (node.rest,)
        if isinstance(node, ast.arg):
            return (node.arg,)
        return ()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node.id in {"revision", "down_revision"}
            and id(node) not in approved_targets
        ):
            raise PublicationWorkerCompatibilityError(
                "trusted Alembic revision metadata contains an unsupported write"
            )
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            try:
                subscript_key = ast.literal_eval(node.slice)
            except (ValueError, TypeError):
                subscript_key = None
            if subscript_key in {"revision", "down_revision"}:
                raise PublicationWorkerCompatibilityError(
                    "trusted Alembic revision metadata contains an unsupported write"
                )
        if any(name in {"revision", "down_revision"} for name in assigned_binding_names(node)):
            raise PublicationWorkerCompatibilityError(
                "trusted Alembic revision metadata contains an unsupported write"
            )
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {
                "globals",
                "locals",
                "vars",
                "exec",
                "eval",
                "setattr",
                "delattr",
            }:
                raise PublicationWorkerCompatibilityError(
                    "trusted Alembic revision metadata uses dynamic namespace mutation"
                )
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in {"update", "setdefault", "pop", "clear", "popitem", "__setitem__", "__delitem__"}:
                    keyword_writes_metadata = any(
                        keyword.arg in {"revision", "down_revision"}
                        for keyword in node.keywords
                    )
                    positional_writes_metadata = False
                    if node.args and node.func.attr in {"setdefault", "pop", "__setitem__", "__delitem__"}:
                        try:
                            key = ast.literal_eval(node.args[0])
                        except (ValueError, TypeError):
                            key = None
                        positional_writes_metadata = key in {"revision", "down_revision"}
                    mapping_writes_metadata = any(
                        isinstance(argument, ast.Dict)
                        and any(
                            key is not None
                            and isinstance(key, ast.Constant)
                            and key.value in {"revision", "down_revision"}
                            for key in argument.keys
                        )
                        for argument in (*node.args, *(keyword.value for keyword in node.keywords))
                    )
                    if keyword_writes_metadata or positional_writes_metadata or mapping_writes_metadata:
                        raise PublicationWorkerCompatibilityError(
                            "trusted Alembic revision metadata uses dynamic namespace mutation"
                        )

    if set(expressions) != {"revision", "down_revision"}:
        raise PublicationWorkerCompatibilityError(
            "trusted Alembic revision metadata is incomplete"
        )
    try:
        revision = ast.literal_eval(expressions["revision"])
        down_revision = ast.literal_eval(expressions["down_revision"])
    except (ValueError, TypeError):
        raise PublicationWorkerCompatibilityError(
            "trusted Alembic revision metadata is invalid"
        ) from None
    if not isinstance(revision, str) or not revision:
        raise PublicationWorkerCompatibilityError(
            "trusted Alembic revision identifier is invalid"
        )
    if down_revision is None:
        parents: tuple[str, ...] = ()
    elif isinstance(down_revision, str) and down_revision:
        parents = (down_revision,)
    elif (
        isinstance(down_revision, tuple)
        and len(down_revision) == 1
        and isinstance(down_revision[0], str)
        and down_revision[0]
    ):
        parents = (down_revision[0],)
    else:
        raise PublicationWorkerCompatibilityError(
            "trusted Alembic parent revision is invalid"
        )
    return revision, parents


def _migration_graph(
    verified_source_files: Mapping[str, bytes],
) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    revision_paths = sorted(
        path
        for path in verified_source_files
        if (
            path.startswith(_MIGRATION_VERSIONS_PREFIX)
            and path.endswith(".py")
            and Path(path).name != "__init__.py"
        )
    )
    revisions: dict[str, tuple[str, ...]] = {}
    for path in revision_paths:
        revision, parents = _parse_revision_metadata(path, verified_source_files[path])
        if revision in revisions:
            raise PublicationWorkerCompatibilityError(
                "trusted Alembic revisions are ambiguous"
            )
        revisions[revision] = parents
    heads = _validate_migration_graph(revisions)
    return revisions, heads


def _is_ancestor(ancestor: str, revision: str, revisions: Mapping[str, tuple[str, ...]]) -> bool:
    pending = [revision]
    visited = set()
    while pending:
        current = pending.pop()
        if current == ancestor:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(revisions[current])
    return False


def _database_revision(database_url: str) -> str:
    normalized = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    normalized = normalized.replace("postgres+asyncpg://", "postgresql://", 1)
    try:
        with psycopg.connect(normalized, connect_timeout=3) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='3s'")
            rows = connection.execute("SELECT version_num FROM public.alembic_version").fetchall()
    except Exception as error:
        raise PublicationWorkerCompatibilityError("database migration state is unavailable") from error
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise PublicationWorkerCompatibilityError("database migration state is ambiguous")
    return rows[0][0]


def _validate_start_mode(
    start_mode: PublicationWorkerStartMode,
    rollback_workspace_id: UUID | None,
    rollback_operation_ids: Mapping[str, UUID] | None,
) -> tuple[UUID | None, dict[str, UUID] | None]:
    if start_mode not in ("current", "rollback-restart"):
        raise PublicationWorkerCompatibilityError("explicit publication worker start mode is required")
    if start_mode == "current":
        if rollback_workspace_id is not None or rollback_operation_ids is not None:
            raise PublicationWorkerCompatibilityError("current mode rejects rollback verification arguments")
        return None, None
    if rollback_workspace_id is None or not isinstance(rollback_operation_ids, Mapping):
        raise PublicationWorkerCompatibilityError("rollback restart requires workspace and both hold IDs")
    if set(rollback_operation_ids) != set(_RECOVERY_REASON_ORDER):
        raise PublicationWorkerCompatibilityError("rollback restart requires exactly both hold reasons")
    try:
        workspace = UUID(str(rollback_workspace_id))
        operations = {reason: UUID(str(rollback_operation_ids[reason])) for reason in _RECOVERY_REASON_ORDER}
    except (TypeError, ValueError, AttributeError):
        raise PublicationWorkerCompatibilityError("rollback restart requires valid UUIDs") from None
    if len(set(operations.values())) != 2:
        raise PublicationWorkerCompatibilityError("rollback hold operation IDs must be distinct")
    return workspace, operations


def require_publication_worker_compatibility(
    database_url: str,
    *,
    start_mode: PublicationWorkerStartMode,
    launch_lock: PublicationWorkerLaunchLock,
    observed_launch_lock_sha256: str,
    rollback_workspace_id: UUID | None = None,
    rollback_operation_ids: Mapping[str, UUID] | None = None,
) -> PublicationWorkerPin:
    """Require an authenticated exact pin at schema 0043 or a qualified descendant."""
    if launch_lock is None:
        raise PublicationWorkerCompatibilityError("publication worker launch-lock configuration is required")
    workspace, operation_ids = _validate_start_mode(
        start_mode, rollback_workspace_id, rollback_operation_ids
    )
    verified_source_files = _validate_lock_and_pin(
        launch_lock, observed_launch_lock_sha256
    )
    revisions, _heads = _migration_graph(verified_source_files)
    revision = _database_revision(database_url)
    if revision not in revisions:
        raise PublicationWorkerCompatibilityError("database migration revision is unknown to this artifact")
    if not _is_ancestor(_MIGRATION_FLOOR_REVISION, revision, revisions):
        raise PublicationWorkerCompatibilityError(
            "database schema floor 0043 is not satisfied"
        )
    if start_mode == "rollback-restart":
        try:
            from salience.cycles.publication_recovery import verify_publication_recovery_rollback

            verify_publication_recovery_rollback(
                database_url,
                workspace_id=workspace,
                operation_ids=operation_ids,
            )
        except PublicationWorkerCompatibilityError:
            raise
        except Exception as error:
            raise PublicationWorkerCompatibilityError(
                "both retained publication recovery holds failed verification"
            ) from error
    return launch_lock.pin


def require_publication_worker_compatibility_from_environment(
    database_url: str,
) -> PublicationWorkerPin | None:
    """Load fixed, launcher-injected gate configuration and verify this process."""
    mode = os.environ.get(PUBLICATION_WORKER_START_MODE_ENV)
    lock_path = os.environ.get(PUBLICATION_WORKER_LAUNCH_LOCK_ENV)
    observed = os.environ.get(PUBLICATION_WORKER_LAUNCH_LOCK_SHA256_ENV)
    if not mode or not lock_path or not observed:
        return require_publication_worker_compatibility(
            database_url,
            start_mode=mode,  # type: ignore[arg-type]
            launch_lock=None,  # type: ignore[arg-type]
            observed_launch_lock_sha256=observed or "",
        )
    if lock_path != PUBLICATION_WORKER_LOCK_PATH:
        raise PublicationWorkerCompatibilityError("in-process launch-lock path is not fixed")
    try:
        raw = Path(lock_path).read_bytes()
        lock = _launch_lock_from_json(json.loads(raw))
    except Exception as error:
        raise PublicationWorkerCompatibilityError("in-process publication worker launch-lock is invalid") from error
    if _canonical_lock_bytes(lock) != raw:
        raise PublicationWorkerCompatibilityError("in-process launch-lock bytes are not canonical")
    workspace = os.environ.get(PUBLICATION_ROLLBACK_WORKSPACE_ENV)
    expired = os.environ.get(PUBLICATION_EXPIRED_HOLD_OPERATION_ENV)
    cancelled = os.environ.get(PUBLICATION_CANCEL_HOLD_OPERATION_ENV)
    if any((workspace, expired, cancelled)) and not all((workspace, expired, cancelled)):
        raise PublicationWorkerCompatibilityError("rollback verification environment must be complete")
    try:
        operation_ids = (
            {"expired_permit_receipt": UUID(expired), "published_during_cancel": UUID(cancelled)}
            if expired and cancelled
            else None
        )
        rollback_workspace_id = UUID(workspace) if workspace else None
    except (TypeError, ValueError, AttributeError) as error:
        raise PublicationWorkerCompatibilityError(
            "rollback verification environment contains an invalid UUID"
        ) from error
    return require_publication_worker_compatibility(
        database_url,
        start_mode=mode,  # type: ignore[arg-type]
        launch_lock=lock,
        observed_launch_lock_sha256=observed,
        rollback_workspace_id=rollback_workspace_id,
        rollback_operation_ids=operation_ids,
    )


def _launch_lock_from_json(value: object) -> PublicationWorkerLaunchLock:
    if not isinstance(value, dict):
        raise ValueError("launch lock must be an object")
    pin_value = value.get("pin")
    if not isinstance(pin_value, dict):
        raise ValueError("launch lock pin is missing")
    pin = PublicationWorkerPin(
        schema_version=pin_value["schema_version"],
        application_commit=pin_value["application_commit"],
        eligible_anchor=pin_value["eligible_anchor"],
        artifact_path=pin_value["artifact_path"],
        artifact_sha256=pin_value["artifact_sha256"],
        distribution_record_path=pin_value["distribution_record_path"],
        distribution_record_sha256=pin_value["distribution_record_sha256"],
        source_manifest_sha256=pin_value["source_manifest_sha256"],
        source_files=tuple(PublicationWorkerSourcePin(**item) for item in pin_value["source_files"]),
        runtime_imports=tuple(PublicationWorkerImportPin(**item) for item in pin_value["runtime_imports"]),
    )
    workers = {
        name: PublicationWorkerEntrypoint(
            module=entry["module"], argv=tuple(entry["argv"])
        )
        for name, entry in value["workers"].items()
    }
    return PublicationWorkerLaunchLock(
        schema_version=value["schema_version"],
        launcher_sha256=value["launcher_sha256"],
        interpreter_path=value["interpreter_path"],
        interpreter_sha256=value["interpreter_sha256"],
        runtime_root=value["runtime_root"],
        workers=workers,
        pin=pin,
    )
