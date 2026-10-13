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
    }
)
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
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
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
        resolved = path.resolve(strict=True)
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
    if not resolved.is_file():
        raise PublicationWorkerCompatibilityError(f"pinned file is not regular: {path}")
    return resolved


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _validate_record(lock: PublicationWorkerLaunchLock, runtime_root: Path) -> set[str]:
    record_path = _contained_file(Path(lock.pin.distribution_record_path), runtime_root)
    if _sha256_file(record_path) != lock.pin.distribution_record_sha256:
        raise PublicationWorkerCompatibilityError("installed distribution RECORD digest mismatch")
    recorded: set[str] = set()
    try:
        rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    except (OSError, UnicodeError, csv.Error) as error:
        raise PublicationWorkerCompatibilityError("installed distribution RECORD is invalid") from error
    if not rows:
        raise PublicationWorkerCompatibilityError("installed distribution RECORD is empty")
    for row in rows:
        if len(row) != 3:
            raise PublicationWorkerCompatibilityError("installed distribution RECORD row is malformed")
        relative, encoded_digest, size = row
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or not relative:
            raise PublicationWorkerCompatibilityError("installed distribution RECORD path is unsafe")
        if relative in recorded:
            raise PublicationWorkerCompatibilityError("installed distribution RECORD repeats a path")
        recorded.add(relative)
        file_path = _contained_file(runtime_root / candidate, runtime_root)
        content = file_path.read_bytes()
        if relative == str(record_path.relative_to(runtime_root)) and not encoded_digest and not size:
            continue
        if not encoded_digest.startswith("sha256=") or not size.isdecimal():
            raise PublicationWorkerCompatibilityError("installed distribution RECORD hash is missing")
        actual = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        if encoded_digest[7:] != actual or int(size) != len(content):
            raise PublicationWorkerCompatibilityError(
                f"installed distribution RECORD file mismatch: {relative}"
            )
    record_relative = str(record_path.relative_to(runtime_root))
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
    if len(source_paths) != len(set(source_paths)) or not _REQUIRED_SOURCE_PATHS <= set(source_paths):
        raise PublicationWorkerCompatibilityError("publication worker source manifest is incomplete")
    if not _HEX_64.fullmatch(pin.source_manifest_sha256):
        raise PublicationWorkerCompatibilityError("publication worker source manifest hash is malformed")
    if hashlib.sha256(_source_manifest_bytes(pin.source_files)).hexdigest() != pin.source_manifest_sha256:
        raise PublicationWorkerCompatibilityError("publication worker source manifest digest mismatch")
    for source_pin in pin.source_files:
        if not _HEX_40.fullmatch(source_pin.git_blob) or not _HEX_64.fullmatch(source_pin.sha256):
            raise PublicationWorkerCompatibilityError("publication worker source pin hash is malformed")
        source_path = _source_file_path(source_pin.path, runtime_root)
        source_file = (
            source_path.resolve(strict=True)
            if source_pin.path == "scripts/launch-v4-publication-worker.py"
            else _contained_file(source_path, runtime_root)
        )
        content = source_file.read_bytes()
        if _sha256_file(source_file) != source_pin.sha256 or _git_blob_sha1(content) != source_pin.git_blob:
            raise PublicationWorkerCompatibilityError(f"publication worker source pin mismatch: {source_pin.path}")

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


def _validate_migration_graph(
    revisions: Mapping[str, tuple[str, ...]],
) -> set[str]:
    """Require the repository's migrations to form one unbranched lineage."""
    if not revisions or "0042_publication_recovery_holds" not in revisions:
        raise PublicationWorkerCompatibilityError("trusted publication recovery migration is missing")
    if any(parent not in revisions for parents in revisions.values() for parent in parents):
        raise PublicationWorkerCompatibilityError("trusted Alembic migration graph has an unknown parent")
    if any(len(parents) > 1 for parents in revisions.values()):
        raise PublicationWorkerCompatibilityError("trusted Alembic migration graph is ambiguous")

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


def _migration_versions_path() -> Path:
    module_path = Path(__file__).resolve()
    candidates = {
        module_path.parents[2] / "migrations" / "versions",
        module_path.parents[3] / "migrations" / "versions",
    }
    matching = sorted(
        path for path in candidates
        if path.is_dir() and (path / "0042_legacy_publication_recovery_holds.py").is_file()
    )
    if len(matching) != 1:
        raise PublicationWorkerCompatibilityError(
            "trusted Alembic migration graph is unavailable or ambiguous"
        )
    return matching[0]


def _migration_graph() -> tuple[dict[str, tuple[str, ...]], set[str]]:
    root = _migration_versions_path()
    revisions: dict[str, tuple[str, ...]] = {}
    try:
        files = sorted(root.glob("*.py"))
    except OSError as error:
        raise PublicationWorkerCompatibilityError("trusted Alembic migration graph is unavailable") from error
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as error:
            raise PublicationWorkerCompatibilityError("trusted Alembic migration graph is invalid") from error
        values = {}
        declared = set()
        for statement in tree.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                if any(isinstance(target, ast.Name) and target.id in {"revision", "down_revision"} for target in targets):
                    name = next(target.id for target in targets if isinstance(target, ast.Name))
                    declared.add(name)
                    try:
                        values[name] = ast.literal_eval(statement.value)
                    except (ValueError, TypeError):
                        raise PublicationWorkerCompatibilityError("trusted Alembic revision metadata is invalid") from None
        revision = values.get("revision")
        if revision is None:
            continue
        if not isinstance(revision, str) or revision in revisions:
            raise PublicationWorkerCompatibilityError("trusted Alembic revisions are ambiguous")
        if "down_revision" not in declared:
            raise PublicationWorkerCompatibilityError("trusted Alembic parent revision is missing")
        down_revision = values.get("down_revision")
        if down_revision is None:
            parents = ()
        elif isinstance(down_revision, str):
            parents = (down_revision,)
        elif isinstance(down_revision, tuple):
            parents = down_revision
        else:
            raise PublicationWorkerCompatibilityError("trusted Alembic parent revision is invalid")
        if any(not isinstance(parent, str) for parent in parents):
            raise PublicationWorkerCompatibilityError("trusted Alembic parent revision is invalid")
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
) -> PublicationWorkerPin | None:
    """Require an authenticated exact pin whenever the database is at/after 0042."""
    revision = _database_revision(database_url)
    revisions, heads = _migration_graph()
    if revision not in revisions:
        raise PublicationWorkerCompatibilityError("database migration revision is unknown to this artifact")
    floor_revision = "0042_publication_recovery_holds"
    if _is_ancestor(floor_revision, revision, revisions):
        floor_active = True
    elif _is_ancestor(revision, floor_revision, revisions):
        floor_active = False
    else:
        raise PublicationWorkerCompatibilityError("database migration revision is outside the trusted lineage")
    if not floor_active:
        return None
    if launch_lock is None:
        raise PublicationWorkerCompatibilityError("publication worker launch-lock configuration is required")
    workspace, operation_ids = _validate_start_mode(
        start_mode, rollback_workspace_id, rollback_operation_ids
    )
    _validate_lock_and_pin(launch_lock, observed_launch_lock_sha256)
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
