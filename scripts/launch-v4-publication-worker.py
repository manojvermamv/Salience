#!/usr/bin/env python3
"""Authenticate the retained publication-worker artifact before starting it."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID


CONTROL_LAUNCHER_PATH = Path("/run/salience-control/launch-v4-publication-worker.py")
CONTROL_LOCK_PATH = Path("/run/salience-control/publication-worker-launch-lock.json")
CONTROL_LOCK_DIGEST_PATH = Path("/run/salience-control/publication-worker-launch-lock.sha256")
RETAINED_WHEEL_PATH = Path("/opt/salience-artifacts/salience.whl")
FIXED_INTERPRETER_PATH = "/usr/local/bin/python"
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_SOURCE_PATHS = {
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
_REQUIRED_IMPORT_MODULES = {
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
_PREFLIGHT = r"""
import json, sys
from salience.cycles.publication_recovery_compatibility import (
    _launch_lock_from_json,
    require_publication_worker_compatibility,
)
payload = json.load(sys.stdin)
lock = _launch_lock_from_json(payload["launch_lock"])
require_publication_worker_compatibility(
    payload["database_url"],
    start_mode=payload["mode"],
    launch_lock=lock,
    observed_launch_lock_sha256=payload["lock_sha256"],
    rollback_workspace_id=payload.get("workspace_id"),
    rollback_operation_ids=payload.get("operation_ids"),
)
"""


class LauncherError(RuntimeError):
    """The external worker launch did not satisfy its fixed trust contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _inside(path: Path, root: Path) -> Path:
    root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise LauncherError(f"pinned file escapes authenticated runtime root: {path}") from error
    if not resolved.is_file():
        raise LauncherError(f"pinned file is not regular: {path}")
    return resolved


def _source_path(source: str, runtime_root: Path) -> Path:
    if source == "scripts/launch-v4-publication-worker.py":
        return CONTROL_LAUNCHER_PATH
    relative = Path(source)
    if relative.is_absolute() or ".." in relative.parts:
        raise LauncherError("source manifest contains an unsafe path")
    if source.startswith("src/"):
        relative = Path(*relative.parts[1:])
    return runtime_root / relative


def _interpreter_installation_prefix() -> Path:
    try:
        installation_prefix = (
            Path(FIXED_INTERPRETER_PATH).absolute().parent.parent.resolve(strict=True)
        )
    except OSError as error:
        raise LauncherError("fixed interpreter installation prefix is unavailable") from error
    if not installation_prefix.is_dir():
        raise LauncherError("fixed interpreter installation prefix is not a directory")
    return installation_prefix


def _verify_record(pin: dict, root: Path) -> set[str]:
    record_path = _inside(Path(pin["distribution_record_path"]), root)
    if _sha256(record_path) != pin["distribution_record_sha256"]:
        raise LauncherError("installed distribution RECORD digest mismatch")
    installation_prefix = _interpreter_installation_prefix()
    try:
        rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    except (OSError, UnicodeError, csv.Error) as error:
        raise LauncherError("installed distribution RECORD is invalid") from error
    seen_paths = set()
    seen_targets = set()
    normalized_root = root.resolve(strict=True)
    record_relative = str(record_path.relative_to(normalized_root))
    for row in rows:
        if len(row) != 3:
            raise LauncherError("installed distribution RECORD row is malformed")
        relative, encoded_hash, size = row
        item = Path(relative)
        if not relative or item.is_absolute() or relative in seen_paths:
            raise LauncherError("installed distribution RECORD path is unsafe or duplicated")
        seen_paths.add(relative)
        is_record_self = relative == record_relative
        if is_record_self:
            if encoded_hash or size:
                raise LauncherError("installed distribution RECORD self entry must be unhashed")
            actual_path = record_path
        else:
            if (
                not encoded_hash.startswith("sha256=")
                or not size.isascii()
                or not size.isdecimal()
            ):
                raise LauncherError("installed distribution RECORD file hash or size is missing")
            try:
                actual_path = (root / item).resolve(strict=True)
            except OSError as error:
                raise LauncherError(
                    f"installed distribution RECORD file is unavailable: {relative}"
                ) from error
            try:
                actual_path.relative_to(installation_prefix)
            except ValueError as error:
                raise LauncherError(
                    f"installed distribution RECORD path escapes interpreter prefix: {relative}"
                ) from error
            if not actual_path.is_file():
                raise LauncherError(f"installed distribution RECORD path is not a file: {relative}")
            if actual_path == record_path:
                raise LauncherError("installed distribution RECORD path aliases its self entry")
        normalized_target = str(actual_path)
        if normalized_target in seen_targets:
            raise LauncherError("installed distribution RECORD contains a duplicate normalized target")
        seen_targets.add(normalized_target)
        content = actual_path.read_bytes()
        if is_record_self:
            continue
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        if encoded_hash[7:] != digest or size != str(len(content)):
            raise LauncherError(f"installed distribution RECORD file mismatch: {relative}")
    if record_relative not in seen_paths:
        raise LauncherError("installed distribution RECORD omits itself")
    return seen_paths


def _verify_authenticated_lock(raw: bytes, trusted_digest: str) -> tuple[dict, str]:
    if not _HEX_64.fullmatch(trusted_digest):
        raise LauncherError("independent launch-lock digest is malformed")
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != trusted_digest:
        raise LauncherError("launch-lock bytes do not match independent trust root")
    try:
        lock = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise LauncherError("launch-lock JSON is invalid") from error
    canonical = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if canonical != raw:
        raise LauncherError("launch-lock bytes are not canonical")
    if lock.get("schema_version") != "salience.publication-worker-launch-lock.v1":
        raise LauncherError("launch-lock schema is unsupported")
    return lock, actual_digest


def _verify_pin(lock: dict) -> Path:
    pin = lock.get("pin")
    if not isinstance(pin, dict) or pin.get("schema_version") != "salience.publication-worker-pin.v1":
        raise LauncherError("publication worker pin is missing or unsupported")
    if pin.get("eligible_anchor") != "69d7ab5da02cc9b8013f17b3e9aed3f7edb758a6":
        raise LauncherError("publication worker pin is not tied to the eligible anchor")
    if not _HEX_40.fullmatch(pin.get("application_commit", "")):
        raise LauncherError("publication worker application commit is malformed")
    if pin["application_commit"] in {
        "039ddf39cb9b846c205f352e083293fbec6f9fc5",
        "a4b2368cdb463939d093020224a5b5667392aee6",
    }:
        raise LauncherError("publication worker application commit is prohibited")
    if pin.get("artifact_path") != "/opt/salience-artifacts/salience.whl":
        raise LauncherError("publication worker artifact path is not retained")
    if _sha256(RETAINED_WHEEL_PATH) != pin.get("artifact_sha256"):
        raise LauncherError("retained publication worker wheel hash mismatch")

    interpreter = lock.get("interpreter_path")
    if interpreter != FIXED_INTERPRETER_PATH:
        raise LauncherError("publication worker interpreter path is not fixed")
    if _sha256(Path(interpreter)) != lock.get("interpreter_sha256"):
        raise LauncherError("publication worker interpreter hash mismatch")
    root = Path(lock.get("runtime_root", ""))
    if not root.is_absolute():
        raise LauncherError("publication worker runtime root must be absolute")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise LauncherError("publication worker runtime root is not a directory")

    source_files = pin.get("source_files")
    if not isinstance(source_files, list):
        raise LauncherError("publication worker source manifest is invalid")
    source_names = [item.get("path") for item in source_files]
    if len(source_names) != len(set(source_names)) or not _REQUIRED_SOURCE_PATHS <= set(source_names):
        raise LauncherError("publication worker source manifest is incomplete")
    source_manifest = (json.dumps(source_files, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if hashlib.sha256(source_manifest).hexdigest() != pin.get("source_manifest_sha256"):
        raise LauncherError("publication worker source manifest digest mismatch")
    for item in source_files:
        source_name = item["path"]
        source_path = _source_path(source_name, root)
        if source_name != "scripts/launch-v4-publication-worker.py":
            source_path = _inside(source_path, root)
        content = source_path.read_bytes()
        if _sha256(source_path) != item.get("sha256") or _git_blob(content) != item.get("git_blob"):
            raise LauncherError(f"publication worker source pin mismatch: {source_name}")

    record_paths = _verify_record(pin, root)
    imports = pin.get("runtime_imports")
    import_names = [item.get("module") for item in imports] if isinstance(imports, list) else []
    if len(import_names) != len(set(import_names)) or not _REQUIRED_IMPORT_MODULES <= set(import_names):
        raise LauncherError("publication worker runtime import manifest is incomplete")
    for item in imports:
        module = item["module"]
        wheel_path = item["wheel_path"]
        relative = Path(wheel_path)
        if relative.is_absolute() or ".." in relative.parts or wheel_path != module.replace(".", "/") + ".py":
            raise LauncherError(f"publication worker import path is unsafe: {module}")
        resolved = _inside(root / relative, root)
        if wheel_path not in record_paths or str(resolved) != str(Path(item["observed_path"]).resolve(strict=True)):
            raise LauncherError(f"publication worker import path does not match its RECORD pin: {module}")
        if _sha256(resolved) != item.get("sha256"):
            raise LauncherError(f"publication worker import hash mismatch: {module}")

    expected_workers = {
        "deployable": {"module": "salience.workflows.worker", "argv": []},
        "legacy-fixture": {"module": "salience.cycles.legacy_runtime", "argv": []},
    }
    if lock.get("workers") != expected_workers:
        raise LauncherError("publication worker entrypoint is not a closed pin")
    launcher_hash = lock.get("launcher_sha256")
    if not _HEX_64.fullmatch(launcher_hash or "") or _sha256(CONTROL_LAUNCHER_PATH) != launcher_hash:
        raise LauncherError("publication worker launcher source hash mismatch")
    return root


def _environment_for_child(mode: str, workspace: str | None, expired: str | None, cancelled: str | None, lock_digest: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PYTHON") and not key.startswith("SALIENCE_PUBLICATION_")
    }
    environment["SALIENCE_PUBLICATION_WORKER_START_MODE"] = mode
    environment["SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK"] = str(CONTROL_LOCK_PATH)
    environment["SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256"] = lock_digest
    if mode == "rollback-restart":
        environment["SALIENCE_PUBLICATION_ROLLBACK_WORKSPACE_ID"] = workspace
        environment["SALIENCE_PUBLICATION_EXPIRED_HOLD_OPERATION_ID"] = expired
        environment["SALIENCE_PUBLICATION_CANCEL_HOLD_OPERATION_ID"] = cancelled
    return environment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--mode", choices=("current", "rollback-restart"), required=True)
    parser.add_argument("--worker-kind", choices=("deployable", "legacy-fixture"), required=True)
    parser.add_argument("--workspace-id")
    parser.add_argument("--expired-hold-operation-id")
    parser.add_argument("--cancel-hold-operation-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    rollback_values = (
        arguments.workspace_id,
        arguments.expired_hold_operation_id,
        arguments.cancel_hold_operation_id,
    )
    if arguments.mode == "current" and any(rollback_values):
        raise LauncherError("current mode rejects rollback verification arguments")
    if arguments.mode == "rollback-restart":
        if not all(rollback_values):
            raise LauncherError("rollback-restart requires workspace and both hold operation IDs")
        try:
            UUID(arguments.workspace_id)
            expired_id = UUID(arguments.expired_hold_operation_id)
            cancel_id = UUID(arguments.cancel_hold_operation_id)
        except ValueError as error:
            raise LauncherError("rollback-restart requires valid workspace and operation UUIDs") from error
        if expired_id == cancel_id:
            raise LauncherError("rollback-restart operation IDs must be distinct")
    else:
        expired_id = cancel_id = None

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise LauncherError("DATABASE_URL is required for publication worker compatibility")
    try:
        raw_lock = CONTROL_LOCK_PATH.read_bytes()
        trusted_digest = CONTROL_LOCK_DIGEST_PATH.read_text(encoding="ascii").strip()
    except OSError as error:
        raise LauncherError("fixed publication worker control bundle is unavailable") from error
    lock, lock_digest = _verify_authenticated_lock(raw_lock, trusted_digest)
    runtime_root = _verify_pin(lock)
    if not _HEX_64.fullmatch(lock.get("launcher_sha256", "")):
        raise LauncherError("publication worker launcher hash is malformed")
    worker = lock["workers"][arguments.worker_kind]
    interpreter = lock["interpreter_path"]
    environment = _environment_for_child(
        arguments.mode,
        arguments.workspace_id,
        str(expired_id) if expired_id else None,
        str(cancel_id) if cancel_id else None,
        lock_digest,
    )
    payload = {
        "launch_lock": lock,
        "lock_sha256": lock_digest,
        "database_url": database_url,
        "mode": arguments.mode,
        "workspace_id": str(arguments.workspace_id) if arguments.workspace_id else None,
        "operation_ids": (
            {
                "expired_permit_receipt": str(expired_id),
                "published_during_cancel": str(cancel_id),
            }
            if expired_id and cancel_id
            else None
        ),
    }
    try:
        result = subprocess.run(
            [interpreter, "-I", "-c", _PREFLIGHT],
            cwd=runtime_root,
            env=environment,
            input=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LauncherError("publication worker compatibility preflight did not complete") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or "publication worker compatibility preflight failed"
        raise LauncherError(detail[-4000:])

    environment["DATABASE_URL"] = database_url
    os.chdir(runtime_root)
    command = [interpreter, "-I", "-m", worker["module"], *worker["argv"]]
    os.execve(interpreter, command, environment)
    raise LauncherError("publication worker execve returned unexpectedly")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LauncherError as error:
        print(f"publication worker launch denied: {error}", file=sys.stderr)
        raise SystemExit(78) from error
