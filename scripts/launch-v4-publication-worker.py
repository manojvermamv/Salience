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
import stat
import subprocess
import sys
from datetime import datetime, timezone
from uuid import UUID


CONTROL_LAUNCHER_PATH = Path("/run/salience-control/launch-v4-publication-worker.py")
CONTROL_LOCK_PATH = Path("/run/salience-control/publication-worker-launch-lock.json")
CONTROL_LOCK_DIGEST_PATH = Path("/run/salience-control/publication-worker-launch-lock.sha256")
RETAINED_WHEEL_PATH = Path("/opt/salience-artifacts/salience.whl")
RETAINED_WHEEL_ARTIFACT_PATH = "/opt/salience-artifacts/salience.whl"
FIXED_INTERPRETER_PATH = "/usr/local/bin/python"
FIXED_RUNTIME_ROOT = Path("/usr/local/lib/python3.13/site-packages")
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
from uuid import UUID
payload = json.load(sys.stdin)
sys.path.insert(0, payload["runtime_root"])
from salience.cycles.publication_recovery_compatibility import (
    _launch_lock_from_json,
    require_production_worker_compatibility,
    require_publication_worker_compatibility,
)
lock = _launch_lock_from_json(payload["launch_lock"])
if lock.schema_version == "salience.publication-worker-launch-lock.v2":
    require_production_worker_compatibility(
        payload["database_url"],
        launch_lock=lock,
        observed_launch_lock_sha256=payload["lock_sha256"],
        route_id=UUID(payload["route_id"]),
    )
else:
    require_publication_worker_compatibility(
        payload["database_url"],
        start_mode=payload["mode"],
        launch_lock=lock,
        observed_launch_lock_sha256=payload["lock_sha256"],
        rollback_workspace_id=payload.get("workspace_id"),
        rollback_operation_ids=payload.get("operation_ids"),
    )
"""
_WORKER_BOOTSTRAP = r"""
import runpy, sys
runtime_root, module, *module_args = sys.argv[1:]
sys.path.insert(0, runtime_root)
sys.argv = [module, *module_args]
runpy.run_module(module, run_name="__main__", alter_sys=True)
"""


class LauncherError(RuntimeError):
    """The external worker launch did not satisfy its fixed trust contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_isolated_startup() -> None:
    if not (sys.flags.isolated and sys.flags.dont_write_bytecode and sys.flags.no_site):
        raise LauncherError("publication worker launcher requires Python -I -B -S startup")


def _read_fixed_control_file(path: Path, description: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise LauncherError(f"fixed {description} is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise LauncherError(f"fixed {description} is not a regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise LauncherError(f"fixed {description} is unavailable") from error


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
    if not isinstance(lock, dict):
        raise LauncherError("launch-lock root must be an object")
    canonical = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if canonical != raw:
        raise LauncherError("launch-lock bytes are not canonical")
    if lock.get("schema_version") not in {
        "salience.publication-worker-launch-lock.v1",
        "salience.publication-worker-launch-lock.v2",
    }:
        raise LauncherError("launch-lock schema is unsupported")
    return lock, actual_digest


def _verify_pin(lock: dict) -> Path:
    if lock.get("schema_version") == "salience.publication-worker-launch-lock.v2":
        production = lock.get("production")
        if not isinstance(production, dict) or set(production) != {
            "schema_version",
            "migration_head",
            "workflow_allowlist",
            "activity_allowlist",
            "route_bundle",
        }:
            raise LauncherError("production route launch payload is missing or open-ended")
        if (
            production.get("schema_version")
            != "salience.p1-production-worker-launch.v1"
            or not isinstance(production.get("migration_head"), str)
            or not production["migration_head"]
            or not isinstance(production.get("workflow_allowlist"), list)
            or not isinstance(production.get("activity_allowlist"), list)
            or not isinstance(production.get("route_bundle"), dict)
        ):
            raise LauncherError("production route launch payload is malformed")
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
    if pin.get("artifact_path") != RETAINED_WHEEL_ARTIFACT_PATH:
        raise LauncherError("publication worker artifact path is not retained")
    if _sha256(RETAINED_WHEEL_PATH) != pin.get("artifact_sha256"):
        raise LauncherError("retained publication worker wheel hash mismatch")

    interpreter = lock.get("interpreter_path")
    if interpreter != FIXED_INTERPRETER_PATH:
        raise LauncherError("publication worker interpreter path is not fixed")
    if _sha256(Path(interpreter)) != lock.get("interpreter_sha256"):
        raise LauncherError("publication worker interpreter hash mismatch")
    root_value = lock.get("runtime_root")
    if root_value != str(FIXED_RUNTIME_ROOT):
        raise LauncherError("publication worker runtime root does not match the fixed root")
    root = Path(root_value)
    if root.is_symlink():
        raise LauncherError("publication worker runtime root must not be a symlink")
    try:
        fixed_root = FIXED_RUNTIME_ROOT.resolve(strict=True)
        root = root.resolve(strict=True)
    except OSError as error:
        raise LauncherError("publication worker runtime root is unavailable") from error
    if root != fixed_root:
        raise LauncherError("publication worker runtime root does not match the fixed root")
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
    launcher_bytes = _read_fixed_control_file(CONTROL_LAUNCHER_PATH, "control launcher")
    if not _HEX_64.fullmatch(launcher_hash or "") or hashlib.sha256(launcher_bytes).hexdigest() != launcher_hash:
        raise LauncherError("publication worker launcher source hash mismatch")
    _reject_bytecode_cache(root)
    return root


def _reject_bytecode_cache(runtime_root: Path) -> None:
    """Reject cache entries before any application or third-party import."""
    resolved_root = runtime_root.resolve(strict=True)
    pending = [resolved_root]
    visited: set[tuple[int, int]] = set()
    while pending:
        directory = pending.pop()
        try:
            metadata = directory.stat()
        except OSError as error:
            raise LauncherError("authenticated runtime root cannot be scanned") from error
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in visited:
            continue
        visited.add(identity)
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise LauncherError("authenticated runtime root cannot be scanned") from error
        for entry in entries:
            path = Path(entry.path)
            if entry.name.endswith(".pyc"):
                raise LauncherError(f"authenticated runtime root contains a .pyc entry: {path}")
            try:
                if entry.is_symlink():
                    target = path.resolve(strict=True)
                    if target.suffix == ".pyc":
                        raise LauncherError(
                            f"authenticated runtime root contains a symlink to .pyc: {path}"
                        )
                    if target.is_dir():
                        try:
                            target.relative_to(resolved_root)
                        except ValueError as error:
                            raise LauncherError(
                                "authenticated runtime root contains a directory symlink escape"
                            ) from error
                        pending.append(target)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
            except OSError as error:
                raise LauncherError("authenticated runtime root cannot be scanned") from error


def _environment_for_child(
    mode: str,
    workspace: str | None,
    expired: str | None,
    cancelled: str | None,
    lock_digest: str,
    *,
    route_manifest: object | None = None,
    lease_identity: object | None = None,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PYTHON")
        and not key.startswith("SALIENCE_PUBLICATION_")
        and not key.startswith("SALIENCE_PRODUCTION_ROUTE_")
    }
    environment["SALIENCE_PUBLICATION_WORKER_START_MODE"] = mode
    environment["SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK"] = str(CONTROL_LOCK_PATH)
    environment["SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256"] = lock_digest
    if mode == "rollback-restart":
        environment["SALIENCE_PUBLICATION_ROLLBACK_WORKSPACE_ID"] = workspace
        environment["SALIENCE_PUBLICATION_EXPIRED_HOLD_OPERATION_ID"] = expired
        environment["SALIENCE_PUBLICATION_CANCEL_HOLD_OPERATION_ID"] = cancelled
    if route_manifest is not None or lease_identity is not None:
        if route_manifest is None or lease_identity is None:
            raise LauncherError("route manifest and one-use lease identity must be complete")
        environment["SALIENCE_PRODUCTION_ROUTE_MANIFEST"] = json.dumps(
            route_manifest, sort_keys=True, separators=(",", ":")
        )
        environment["SALIENCE_PRODUCTION_ROUTE_LEASE"] = json.dumps(
            lease_identity, sort_keys=True, separators=(",", ":")
        )
    return environment


def _claim_authenticated_route(
    lock: dict,
    lock_digest: str,
    runtime_root: Path,
    *,
    route_id: UUID,
    slot_id: UUID,
    owner_id: UUID,
    route_authority,
):
    """Resolve and claim one route only after the outer lock and preflight pass."""
    if getattr(route_authority, "protocol_version", None) != "salience.p1-route-fence.v1":
        raise LauncherError("production route lease authority version is unsupported")
    sys.path.insert(0, str(runtime_root))
    try:
        from salience.cycles.production_routing import (
            RouteLease,
            RouteLeaseClaim,
            canonical_route_manifest_bytes,
            parse_route_bundle,
            route_manifest_sha256,
        )

        routes = parse_route_bundle(
            lock["production"]["route_bundle"],
            expected_lock_sha256=lock_digest,
        )
        selected = tuple(route for route in routes if route.route_id == route_id)
        if len(selected) != 1:
            raise LauncherError(
                "requested route is absent or ambiguous in the authenticated bundle"
            )
        route = selected[0]
        if slot_id not in route.slots:
            raise LauncherError("requested slot is absent from the authenticated route")
        expected_manifest_digest = route_manifest_sha256(route)
        lease = route_authority.claim_slot(
            RouteLeaseClaim(
                route_id=route.route_id,
                route_manifest_sha256=expected_manifest_digest,
                artifact_lock_sha256=lock_digest,
                slot_id=slot_id,
                owner_id=owner_id,
                build_id=route.worker_build_id,
            )
        )
        if (
            not isinstance(lease, RouteLease)
            or lease.route_id != route.route_id
            or lease.slot_id != slot_id
            or lease.owner_id != owner_id
            or lease.route_manifest_sha256 != expected_manifest_digest
            or lease.artifact_lock_sha256 != lock_digest
            or lease.expires_at <= datetime.now(timezone.utc)
        ):
            raise LauncherError("route authority returned a mismatched one-use lease identity")
        readback = route_authority.read_slot(route.route_id, slot_id)
        if (
            not readback.available
            or readback.state != "leased"
            or readback.lease != lease
        ):
            raise LauncherError("route authority did not read back its claimed slot")
        manifest_payload = json.loads(canonical_route_manifest_bytes(route))
        lease_payload = {
            "route_id": str(lease.route_id),
            "slot_id": str(lease.slot_id),
            "owner_id": str(lease.owner_id),
            "fencing_epoch": lease.fencing_epoch,
            "expires_at": lease.expires_at.isoformat(),
            "route_manifest_sha256": lease.route_manifest_sha256,
            "artifact_lock_sha256": lease.artifact_lock_sha256,
        }
        return route, lease, manifest_payload, lease_payload
    except LauncherError:
        raise
    except Exception as error:
        raise LauncherError("production route lease claim was denied") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--mode", choices=("current", "rollback-restart"), required=True)
    parser.add_argument("--worker-kind", choices=("deployable", "legacy-fixture"), required=True)
    parser.add_argument("--workspace-id")
    parser.add_argument("--expired-hold-operation-id")
    parser.add_argument("--cancel-hold-operation-id")
    parser.add_argument("--route-id")
    parser.add_argument("--slot-id")
    parser.add_argument("--owner-id")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    route_authority=None,
    route_child_exec=None,
) -> int:
    _require_isolated_startup()
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
    route_values = (arguments.route_id, arguments.slot_id, arguments.owner_id)
    if any(route_values) and not all(route_values):
        raise LauncherError("routed startup requires route, slot, and owner UUIDs")
    if all(route_values):
        try:
            requested_route_id = UUID(arguments.route_id)
            requested_slot_id = UUID(arguments.slot_id)
            requested_owner_id = UUID(arguments.owner_id)
        except ValueError as error:
            raise LauncherError(
                "routed startup requires valid route, slot, and owner UUIDs"
            ) from error
    else:
        requested_route_id = requested_slot_id = requested_owner_id = None

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise LauncherError("DATABASE_URL is required for publication worker compatibility")
    if arguments.worker_kind == "legacy-fixture":
        test_database_url = os.environ.get("TEST_DATABASE_URL")
        if not test_database_url or test_database_url != database_url:
            raise LauncherError(
                "legacy fixture TEST_DATABASE_URL must match the checked DATABASE_URL"
            )
    try:
        raw_lock = _read_fixed_control_file(CONTROL_LOCK_PATH, "launch lock")
        raw_digest = _read_fixed_control_file(CONTROL_LOCK_DIGEST_PATH, "launch-lock digest")
        digest_text = raw_digest.decode("ascii")
    except (OSError, UnicodeError) as error:
        raise LauncherError("fixed publication worker control bundle is unavailable") from error
    if not digest_text.endswith("\n") or not _HEX_64.fullmatch(digest_text[:-1]):
        raise LauncherError("independent launch-lock digest is malformed")
    lock, lock_digest = _verify_authenticated_lock(raw_lock, digest_text[:-1])
    try:
        runtime_root = _verify_pin(lock)
    except LauncherError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise LauncherError("publication worker launch pin is invalid") from error
    if not _HEX_64.fullmatch(lock.get("launcher_sha256", "")):
        raise LauncherError("publication worker launcher hash is malformed")
    is_routed = lock["schema_version"] == "salience.publication-worker-launch-lock.v2"
    if is_routed:
        if arguments.mode != "current" or arguments.worker_kind != "deployable":
            raise LauncherError("production route startup requires current deployable worker mode")
        if requested_route_id is None or requested_slot_id is None or requested_owner_id is None:
            raise LauncherError("production route startup requires route, slot, and owner UUIDs")
        if route_authority is None:
            raise LauncherError(
                "production route startup requires an injected route lease authority"
            )
        if route_child_exec is None:
            raise LauncherError("production route child handoff requires an injected executor")
    elif any(route_values) or route_authority is not None or route_child_exec is not None:
        raise LauncherError("v1 fixture launch rejects production route inputs")
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
        "runtime_root": str(runtime_root),
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
        "route_id": str(requested_route_id) if requested_route_id else None,
    }
    try:
        result = subprocess.run(
            [interpreter, "-I", "-B", "-S", "-c", _PREFLIGHT],
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

    route = lease = route_manifest_payload = lease_identity_payload = None
    if is_routed:
        route, lease, route_manifest_payload, lease_identity_payload = (
            _claim_authenticated_route(
                lock,
                lock_digest,
                runtime_root,
                route_id=requested_route_id,
                slot_id=requested_slot_id,
                owner_id=requested_owner_id,
                route_authority=route_authority,
            )
        )
        environment = _environment_for_child(
            arguments.mode,
            arguments.workspace_id,
            str(expired_id) if expired_id else None,
            str(cancel_id) if cancel_id else None,
            lock_digest,
            route_manifest=route_manifest_payload,
            lease_identity=lease_identity_payload,
        )

    environment["DATABASE_URL"] = database_url
    os.chdir(runtime_root)
    command = [
        interpreter,
        "-I",
        "-B",
        "-S",
        "-c",
        _WORKER_BOOTSTRAP,
        str(runtime_root),
        worker["module"],
        *worker["argv"],
    ]
    if is_routed:
        if route_child_exec is None:
            raise LauncherError("production route child handoff requires an injected executor")
        route_child_exec(interpreter, command, environment)
        raise LauncherError("injected production route child executor returned unexpectedly")
    try:
        os.execve(interpreter, command, environment)
    except OSError as error:
        raise LauncherError("publication worker execve failed") from error
    raise LauncherError("publication worker execve returned unexpectedly")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LauncherError as error:
        print(f"publication worker launch denied: {error}", file=sys.stderr)
        raise SystemExit(78) from error
