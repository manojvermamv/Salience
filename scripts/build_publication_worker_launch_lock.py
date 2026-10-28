#!/usr/bin/env python3
"""Build an authenticated publication-worker launch lock and control bundle."""

from __future__ import annotations

import argparse
import base64
import csv
from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TARGET_RUNTIME_ROOT = "/usr/local/lib/python3.13/site-packages"
TARGET_INTERPRETER_PATH = "/usr/local/bin/python"
ARTIFACT_PATH = "/opt/salience-artifacts/salience.whl"
CONTROL_LOCK_NAME = "publication-worker-launch-lock.json"
CONTROL_DIGEST_NAME = "publication-worker-launch-lock.sha256"
CONTROL_LAUNCHER_NAME = "launch-v4-publication-worker.py"
LAUNCHER_SOURCE_PATH = "scripts/launch-v4-publication-worker.py"
SOURCE_ROOT = "src/salience/"
MIGRATION_ROOT = "migrations/"
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _load_compatibility_types():
    source_root = str(REPOSITORY_ROOT / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from salience.cycles import publication_recovery_compatibility as compatibility

    return compatibility


_compatibility = _load_compatibility_types()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _run_git(*arguments: str, cwd: Path) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("application commit or tracked source is unavailable") from error
    return result.stdout


def _validate_commit(application_commit: str, repository_root: Path) -> None:
    if not isinstance(application_commit, str) or not _HEX_40.fullmatch(application_commit):
        raise ValueError("application_commit must be a full lowercase Git commit SHA")
    resolved = _run_git("rev-parse", "--verify", f"{application_commit}^{{commit}}", cwd=repository_root)
    if resolved.decode("ascii").strip() != application_commit:
        raise ValueError("application_commit does not identify the requested Git commit")
    head = _run_git("rev-parse", "--verify", "HEAD", cwd=repository_root).decode("ascii").strip()
    if head != application_commit:
        raise ValueError("repository HEAD does not match application_commit")


def _tracked_tree(application_commit: str, repository_root: Path) -> dict[str, bytes]:
    raw_names = _run_git(
        "ls-tree", "-r", "--name-only", "-z", application_commit, cwd=repository_root
    )
    selected = {
        name.decode("utf-8")
        for name in raw_names.split(b"\0")
        if name
        and (
            name.startswith(SOURCE_ROOT.encode())
            or name.startswith(MIGRATION_ROOT.encode())
            or name == b"alembic.ini"
            or name.decode("utf-8") == LAUNCHER_SOURCE_PATH
        )
    }
    required = set(_compatibility._REQUIRED_SOURCE_PATHS)
    missing = required - selected
    if missing:
        raise ValueError("application commit omits required publication-worker source files")

    tree: dict[str, bytes] = {}
    for name in sorted(selected):
        if name.startswith("src/") and ("/__pycache__/" in name or name.endswith(".pyc")):
            continue
        if name.startswith("migrations/") and ("/__pycache__/" in name or name.endswith(".pyc")):
            continue
        try:
            committed = _run_git("show", f"{application_commit}:{name}", cwd=repository_root)
        except ValueError as error:
            raise ValueError(f"application commit source is unavailable: {name}") from error
        source_path = _safe_repository_file(repository_root, name)
        if source_path.read_bytes() != committed:
            raise ValueError(f"tracked source differs from application_commit: {name}")
        tree[name] = committed

    revision_names = {
        name
        for name in tree
        if name.startswith("migrations/versions/")
        and name.endswith(".py")
        and PurePosixPath(name).name != "__init__.py"
    }
    numbered: dict[int, list[str]] = {}
    for name in revision_names:
        match = re.fullmatch(r"\d{4}_.+\.py", PurePosixPath(name).name)
        if match is None:
            raise ValueError(f"migration revision filename is malformed: {name}")
        numbered.setdefault(int(PurePosixPath(name).name[:4]), []).append(name)
    if any(len(numbered.get(number, ())) != 1 for number in range(1, 44)):
        raise ValueError("application commit must contain exactly one migration revision from 0001 through 0043")
    return tree


def _safe_repository_file(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or not relative or ".." in posix.parts or "\\" in relative:
        raise ValueError(f"source path is unsafe: {relative}")
    current = root
    for part in posix.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise ValueError(f"tracked source file is unavailable: {relative}") from error
        if stat.S_ISLNK(mode):
            raise ValueError(f"tracked source path uses a symlink: {relative}")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise ValueError(f"tracked source is not a regular file: {relative}")
    return current


def _runtime_inventory(runtime_root: Path, expected: set[str], prefix: str) -> None:
    root = runtime_root / prefix.rstrip("/")
    if not root.exists() or root.is_symlink() or not root.is_dir():
        raise ValueError(f"installed runtime inventory is unavailable: {root}")
    actual: set[str] = set()
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for dirname in tuple(dirnames):
            child = directory_path / dirname
            if child.is_symlink():
                raise ValueError(f"installed runtime inventory contains a symlink: {child}")
            if dirname == "__pycache__":
                dirnames.remove(dirname)
        for filename in filenames:
            child = directory_path / filename
            try:
                mode = child.lstat().st_mode
            except OSError as error:
                raise ValueError(f"installed runtime file is unavailable: {child}") from error
            if stat.S_ISLNK(mode):
                raise ValueError(f"installed runtime inventory contains a symlink: {child}")
            if not stat.S_ISREG(mode):
                raise ValueError(f"installed runtime file is not regular: {child}")
            if filename.endswith(".pyc"):
                continue
            actual.add(child.relative_to(runtime_root).as_posix())
    if actual != expected:
        raise ValueError(f"installed runtime inventory does not match application_commit: {prefix}")


def _validate_installed_source_tree(tree: dict[str, bytes], runtime_root: Path) -> None:
    package_paths = {name for name in tree if name.startswith(SOURCE_ROOT)}
    migration_paths = {name for name in tree if name.startswith(MIGRATION_ROOT)}
    _runtime_inventory(runtime_root, {name.removeprefix("src/") for name in package_paths}, "salience")
    _runtime_inventory(runtime_root, migration_paths, "migrations")

    config_path = runtime_root / "alembic.ini"
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("installed Alembic configuration is unavailable")
    if config_path.read_bytes() != tree["alembic.ini"]:
        raise ValueError("installed Alembic configuration differs from application_commit")
    for name in sorted(package_paths | migration_paths):
        runtime_name = name.removeprefix("src/")
        target = runtime_root / runtime_name
        if target.is_symlink() or not target.is_file() or target.read_bytes() != tree[name]:
            raise ValueError(f"installed runtime source differs from application_commit: {name}")


def _safe_wheel_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _record_rows(
    record_bytes: bytes, label: str, *, allow_parent_paths: bool = False
) -> dict[str, tuple[str, str]]:
    try:
        rows = list(csv.reader(io.StringIO(record_bytes.decode("utf-8"))))
    except (UnicodeError, csv.Error) as error:
        raise ValueError(f"{label} is malformed") from error
    result: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3:
            raise ValueError(f"{label} contains a malformed row")
        name, digest, size = row
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or path.as_posix() != name
            or "\\" in name
            or (".." in path.parts and not allow_parent_paths)
            or name in result
        ):
            raise ValueError(f"{label} contains an unsafe or duplicate path")
        result[name] = (digest, size)
    if not result:
        raise ValueError(f"{label} is empty")
    return result


def _record_digest(content: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode("ascii").rstrip("=")


def _record_target(runtime_root: Path, relative: str) -> Path:
    relative_path = PurePosixPath(relative)
    if ".." in relative_path.parts:
        if (
            runtime_root.name != "site-packages"
            or not runtime_root.parent.name.startswith("python")
            or runtime_root.parent.parent.name != "lib"
        ):
            raise ValueError("installed RECORD path escapes runtime root")
        installation_prefix = runtime_root.parents[2].resolve(strict=True)
    else:
        installation_prefix = runtime_root.resolve(strict=True)
    candidate = runtime_root.joinpath(*relative_path.parts)
    try:
        lexical = Path(os.path.abspath(candidate))
        lexical_relative = lexical.relative_to(installation_prefix)
        current = installation_prefix
        for component in lexical_relative.parts:
            current = current / component
            if current.is_symlink():
                raise ValueError(f"installed RECORD path uses a symlink: {relative}")
        resolved = candidate.resolve(strict=True)
        if resolved != lexical:
            raise ValueError(f"installed RECORD path resolves through a symlink: {relative}")
        resolved.relative_to(installation_prefix)
    except (OSError, ValueError) as error:
        raise ValueError(f"installed RECORD path is unavailable or escapes its prefix: {relative}") from error
    current = installation_prefix
    try:
        components = resolved.relative_to(installation_prefix).parts
    except ValueError as error:
        raise ValueError(f"installed RECORD path escapes its prefix: {relative}") from error
    for component in components:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"installed RECORD path uses a symlink: {relative}")
    if not stat.S_ISREG(resolved.lstat().st_mode):
        raise ValueError(f"installed RECORD path is not a regular file: {relative}")
    return resolved


def _validate_wheel_and_installed_record(
    wheel_path: Path,
    runtime_root: Path,
    expected_package: dict[str, bytes],
) -> tuple[str, bytes]:
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any(not _safe_wheel_name(name) for name in names):
                raise ValueError("wheel has duplicate or unsafe members")
            for item in archive.infolist():
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError(f"wheel contains a symlink member: {item.filename}")
            wheel_files = {name for name in names if not name.endswith("/")}
            wheel_package = {name for name in wheel_files if name.startswith("salience/")}
            if wheel_package != set(expected_package):
                raise ValueError("wheel package inventory does not match application_commit")
            for name, expected_bytes in expected_package.items():
                if archive.read(name) != expected_bytes:
                    raise ValueError(f"wheel source differs from application_commit: {name}")
            record_paths = sorted(
                name for name in wheel_files
                if name.endswith(".dist-info/RECORD") and name.count("/") == 1
            )
            if len(record_paths) != 1:
                raise ValueError("wheel must contain exactly one distribution RECORD")
            wheel_record_path = record_paths[0]
            wheel_record_bytes = archive.read(wheel_record_path)
            wheel_rows = _record_rows(wheel_record_bytes, "wheel RECORD")
            if set(wheel_rows) != wheel_files:
                raise ValueError("wheel RECORD does not cover the exact wheel inventory")
            for name, (digest, size) in wheel_rows.items():
                if name == wheel_record_path:
                    if digest or size:
                        raise ValueError("wheel RECORD self row must be unhashed")
                    continue
                content = archive.read(name)
                if digest != "sha256=" + _record_digest(content) or size != str(len(content)):
                    raise ValueError(f"wheel RECORD entry does not match wheel member: {name}")
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise ValueError("publication worker wheel is invalid") from error

    runtime_record = runtime_root / wheel_record_path
    if runtime_record.is_symlink() or not runtime_record.is_file():
        raise ValueError("installed distribution RECORD is unavailable")
    record_bytes = runtime_record.read_bytes()
    installed_rows = _record_rows(
        record_bytes, "installed RECORD", allow_parent_paths=True
    )
    record_relative = runtime_record.relative_to(runtime_root).as_posix()
    if record_relative not in installed_rows or installed_rows[record_relative] != ("", ""):
        raise ValueError("installed RECORD must contain an unhashed self row")
    installed_package_names = {name for name in installed_rows if name.startswith("salience/")}
    if installed_package_names != set(expected_package):
        raise ValueError("installed RECORD does not cover the exact application package")

    normalized_targets: set[Path] = set()
    for name, (digest, size) in installed_rows.items():
        if name == record_relative:
            continue
        candidate = _record_target(runtime_root, name)
        if candidate in normalized_targets:
            raise ValueError("installed RECORD repeats a normalized target")
        normalized_targets.add(candidate)
        content = candidate.read_bytes()
        if digest != "sha256=" + _record_digest(content) or size != str(len(content)):
            raise ValueError(f"installed RECORD entry does not match runtime file: {name}")
    for name, expected_bytes in expected_package.items():
        if (runtime_root / name).read_bytes() != expected_bytes:
            raise ValueError(f"installed package differs from wheel: {name}")
    return record_relative, record_bytes


def _write_staged_file(path: Path, content: bytes, mode: int) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)
    if path.read_bytes() != content:
        raise OSError(f"staged output failed byte validation: {path.name}")


def _publish_output_bundle(outputs: list[tuple[Path, bytes]]) -> None:
    """Stage all outputs, then publish as a rollback-capable replacement set."""
    normalized_outputs: list[tuple[Path, bytes]] = []
    seen: set[Path] = set()
    for requested_path, content in outputs:
        requested_path = Path(requested_path)
        requested_path.parent.mkdir(parents=True, exist_ok=True)
        parent = requested_path.parent.resolve(strict=True)
        target = parent / requested_path.name
        if target in seen:
            raise ValueError("launch-lock output paths must be distinct")
        seen.add(target)
        if target.is_symlink():
            raise ValueError(f"launch-lock output must not be a symlink: {target}")
        if target.exists() and not stat.S_ISREG(target.lstat().st_mode):
            raise ValueError(f"launch-lock output is not a regular file: {target}")
        normalized_outputs.append((target, content))

    snapshots: dict[Path, tuple[bool, bytes | None, int]] = {}
    for target, _content in normalized_outputs:
        exists = target.exists()
        if exists:
            status = target.lstat()
            snapshots[target] = (True, target.read_bytes(), stat.S_IMODE(status.st_mode))
        else:
            snapshots[target] = (False, None, 0o644)

    stage_directories: dict[Path, Path] = {}
    entries: list[dict[str, object]] = []
    preserve_staging = False
    try:
        for target, _content in normalized_outputs:
            if target.parent not in stage_directories:
                stage_directories[target.parent] = Path(
                    tempfile.mkdtemp(
                        prefix=".publication-worker-launch-lock-stage-",
                        dir=target.parent,
                    )
                )

        for index, (target, content) in enumerate(normalized_outputs):
            stage_directory = stage_directories[target.parent]
            staged = stage_directory / f"output-{index}.new"
            was_present, prior_bytes, prior_mode = snapshots[target]
            _write_staged_file(staged, content, prior_mode)
            backup = None
            if was_present:
                backup = stage_directory / f"output-{index}.prior"
                _write_staged_file(backup, prior_bytes or b"", prior_mode)
            entries.append(
                {
                    "target": target,
                    "staged": staged,
                    "backup": backup,
                    "content": content,
                    "was_present": was_present,
                    "mode": prior_mode,
                }
            )

        recovery_manifest = _canonical_json(
            [
                {
                    "target": str(entry["target"]),
                    "was_present": entry["was_present"],
                    "mode": entry["mode"],
                    "backup": str(entry["backup"]) if entry["backup"] is not None else None,
                }
                for entry in entries
            ]
        )
        # Every stage directory carries the same map so the retained prior files
        # remain usable even if the caller only recovers one output directory.
        for stage_directory in stage_directories.values():
            manifest_path = stage_directory / "recovery-manifest.json"
            _write_staged_file(manifest_path, recovery_manifest, 0o600)

        # Confirm the published set is unchanged after staging and before the first rename.
        for target, _content in normalized_outputs:
            was_present, prior_bytes, prior_mode = snapshots[target]
            if was_present:
                if (
                    target.is_symlink()
                    or not target.is_file()
                    or target.read_bytes() != prior_bytes
                    or stat.S_IMODE(target.lstat().st_mode) != prior_mode
                ):
                    raise OSError(f"launch-lock output changed during staging: {target}")
            elif target.exists() or target.is_symlink():
                raise OSError(f"launch-lock output appeared during staging: {target}")

        replaced: list[dict[str, object]] = []
        current_entry: dict[str, object] | None = None
        try:
            for entry in entries:
                current_entry = entry
                os.replace(entry["staged"], entry["target"])
                replaced.append(entry)
                current_entry = None
        except BaseException as publish_error:
            # Include a rename that completed before an injected or I/O exception was raised.
            if current_entry is not None and not Path(current_entry["staged"]).exists():
                if Path(current_entry["target"]).exists():
                    replaced.append(current_entry)
            rollback_errors = []
            for entry in reversed(replaced):
                target = Path(entry["target"])
                backup = entry["backup"]
                try:
                    if backup is not None:
                        restore_copy = backup.with_suffix(".restore")
                        _write_staged_file(restore_copy, backup.read_bytes(), int(entry["mode"]))
                        os.replace(restore_copy, target)
                    else:
                        target.unlink(missing_ok=True)
                except OSError as rollback_error:
                    rollback_errors.append(rollback_error)
            if rollback_errors:
                preserve_staging = True
                recovery_paths = list(stage_directories.values())
                recovery_paths.extend(
                    directory / "recovery-manifest.json"
                    for directory in stage_directories.values()
                )
                recovery_paths.extend(
                    Path(entry["backup"])
                    for entry in entries
                    if entry["backup"] is not None
                )
                recovery_paths.extend(
                    path
                    for directory in stage_directories.values()
                    for path in directory.glob("*.restore")
                )
                rendered_paths = ", ".join(str(path) for path in recovery_paths)
                rendered_errors = "; ".join(str(error) for error in rollback_errors)
                raise RuntimeError(
                    "launch-lock output publication failed; rollback was incomplete; "
                    f"recovery artifacts preserved at: {rendered_paths}; "
                    f"restore errors: {rendered_errors}"
                ) from publish_error
            raise
    finally:
        if not preserve_staging:
            for stage_directory in stage_directories.values():
                shutil.rmtree(stage_directory, ignore_errors=True)


def build_publication_worker_launch_lock(
    *,
    application_commit: str,
    wheel: Path,
    runtime_root: Path,
    lock_path: Path,
    independent_digest_path: Path,
    control_dir: Path,
    interpreter_source: Path | None = None,
    route_bundle: Mapping[str, object] | None = None,
) -> _compatibility.PublicationWorkerLaunchLockAny:
    """Validate a candidate install and write its canonical lock/control bundle."""
    repository_root = Path(REPOSITORY_ROOT).resolve(strict=True)
    _validate_commit(application_commit, repository_root)
    tree = _tracked_tree(application_commit, repository_root)
    runtime_root = Path(runtime_root)
    if runtime_root.is_symlink():
        raise ValueError("runtime_root must not be a symlink")
    runtime_root = runtime_root.resolve(strict=True)
    if not runtime_root.is_dir():
        raise ValueError("runtime_root must be a directory")
    _validate_installed_source_tree(tree, runtime_root)

    wheel = Path(wheel)
    if wheel.is_symlink() or not wheel.is_file():
        raise ValueError("wheel must be a regular file")
    wheel = wheel.resolve(strict=True)
    expected_package = {
        name.removeprefix("src/"): content
        for name, content in tree.items()
        if name.startswith(SOURCE_ROOT)
    }
    record_relative, record_bytes = _validate_wheel_and_installed_record(
        wheel, runtime_root, expected_package
    )

    source_pins = tuple(
        _compatibility.PublicationWorkerSourcePin(
            path=name,
            git_blob=_git_blob_sha1(content),
            sha256=_sha256(content),
        )
        for name, content in sorted(tree.items())
    )
    revisions: dict[str, tuple[str, ...]] = {}
    for name, content in sorted(tree.items()):
        if (
            name.startswith("migrations/versions/")
            and name.endswith(".py")
            and PurePosixPath(name).name != "__init__.py"
        ):
            revision, parents = _compatibility._parse_revision_metadata(name, content)
            if revision in revisions:
                raise ValueError("application commit contains duplicate Alembic revision IDs")
            revisions[revision] = parents
    migration_heads = _compatibility._validate_migration_graph(revisions)
    migration_head = next(iter(migration_heads))
    if route_bundle is not None:
        from salience.cycles.production_routing import (
            PRODUCTION_ACTIVITY_ALLOWLIST,
            PRODUCTION_WORKFLOW_ALLOWLIST,
            parse_route_bundle,
        )

        if migration_head != _compatibility.PRODUCTION_MIGRATION_HEAD:
            raise ValueError("production launch lock requires the exact current migration head")
        try:
            routes = parse_route_bundle(route_bundle, expected_lock_sha256="0" * 64)
        except (TypeError, ValueError) as error:
            raise ValueError("production route bundle is invalid") from error
        artifact_sha256 = _sha256(wheel.read_bytes())
        for route in routes:
            if (
                route.route_tuple.worker_kind != "production-publication"
                or set(route.route_tuple.workflow_types) != set(PRODUCTION_WORKFLOW_ALLOWLIST)
                or set(route.route_tuple.activity_types) != set(PRODUCTION_ACTIVITY_ALLOWLIST)
                or route.artifact_sha256 != artifact_sha256
            ):
                raise ValueError("production route does not match the closed artifact allowlists")
    source_manifest_sha256 = _sha256(_canonical_json([asdict(item) for item in source_pins]))
    required_import_modules = set(_compatibility._REQUIRED_IMPORT_MODULES)
    if route_bundle is not None:
        required_import_modules.update(
            {
                "salience.cycles.production_routing",
                "salience.cycles.production_dispatch",
            }
        )
    imports = []
    for module in sorted(required_import_modules):
        wheel_relative = module.replace(".", "/") + ".py"
        content = expected_package.get(wheel_relative)
        if content is None:
            raise ValueError(f"required runtime import is absent from application_commit: {module}")
        installed_path = runtime_root / wheel_relative
        if installed_path.read_bytes() != content:
            raise ValueError(f"required runtime import differs from application_commit: {module}")
        imports.append(
            _compatibility.PublicationWorkerImportPin(
                module=module,
                wheel_path=wheel_relative,
                observed_path=str(Path(TARGET_RUNTIME_ROOT) / wheel_relative),
                sha256=_sha256(content),
            )
        )

    launcher_bytes = tree[LAUNCHER_SOURCE_PATH]
    interpreter_path = Path(TARGET_INTERPRETER_PATH)
    if interpreter_source is None:
        try:
            interpreter_bytes = interpreter_path.read_bytes()
        except OSError as error:
            raise ValueError("target interpreter is unavailable") from error
    else:
        interpreter_source_path = Path(interpreter_source)
        try:
            interpreter_metadata = interpreter_source_path.lstat()
            if (
                not stat.S_ISREG(interpreter_metadata.st_mode)
                or interpreter_source_path.is_symlink()
            ):
                raise ValueError("target interpreter source must be a regular file")
            interpreter_bytes = interpreter_source_path.read_bytes()
        except OSError as error:
            raise ValueError("target interpreter source is unavailable") from error
    pin = _compatibility.PublicationWorkerPin(
        schema_version="salience.publication-worker-pin.v1",
        application_commit=application_commit,
        eligible_anchor=_compatibility.ELIGIBLE_PUBLICATION_WORKER_ANCHOR,
        artifact_path=ARTIFACT_PATH,
        artifact_sha256=_sha256(wheel.read_bytes()),
        distribution_record_path=str(Path(TARGET_RUNTIME_ROOT) / record_relative),
        distribution_record_sha256=_sha256(record_bytes),
        source_manifest_sha256=source_manifest_sha256,
        source_files=source_pins,
        runtime_imports=tuple(imports),
    )
    common_lock_fields = {
        "launcher_sha256": _sha256(launcher_bytes),
        "interpreter_path": str(interpreter_path),
        "interpreter_sha256": _sha256(interpreter_bytes),
        "runtime_root": str(TARGET_RUNTIME_ROOT),
        "workers": {
            "deployable": _compatibility.PublicationWorkerEntrypoint(
                module="salience.workflows.worker", argv=()
            ),
            "legacy-fixture": _compatibility.PublicationWorkerEntrypoint(
                module="salience.cycles.legacy_runtime", argv=()
            ),
        },
        "pin": pin,
    }
    if route_bundle is None:
        lock = _compatibility.PublicationWorkerLaunchLock(
            schema_version="salience.publication-worker-launch-lock.v1",
            **common_lock_fields,
        )
    else:
        from salience.cycles.production_routing import (
            PRODUCTION_ACTIVITY_ALLOWLIST,
            PRODUCTION_WORKFLOW_ALLOWLIST,
        )

        lock = _compatibility.PublicationWorkerLaunchLockV2(
            schema_version="salience.publication-worker-launch-lock.v2",
            production=_compatibility.ProductionWorkerLockPayloadV1(
                schema_version="salience.p1-production-worker-launch.v1",
                migration_head=migration_head,
                workflow_allowlist=PRODUCTION_WORKFLOW_ALLOWLIST,
                activity_allowlist=PRODUCTION_ACTIVITY_ALLOWLIST,
                route_bundle=route_bundle,
            ),
            **common_lock_fields,
        )
    lock_bytes = _canonical_json(asdict(lock))
    digest_bytes = (_sha256(lock_bytes) + "\n").encode("ascii")
    control_dir = Path(control_dir)
    outputs = [
        (Path(lock_path), lock_bytes),
        (Path(independent_digest_path), digest_bytes),
        (control_dir / CONTROL_LOCK_NAME, lock_bytes),
        (control_dir / CONTROL_DIGEST_NAME, digest_bytes),
        (control_dir / CONTROL_LAUNCHER_NAME, launcher_bytes),
    ]
    if digest_bytes != (_sha256(lock_bytes) + "\n").encode("ascii"):
        raise ValueError("independent publication worker lock digest does not match canonical lock")
    if outputs[0][1] != outputs[2][1] or outputs[1][1] != outputs[3][1]:
        raise ValueError("publication worker control bundle is not byte-identical")
    _publish_output_bundle(outputs)
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-commit", required=True)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--independent-digest", required=True, type=Path)
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument(
        "--interpreter-source",
        type=Path,
        help=(
            "optional host path to the exact target-image interpreter bytes; "
            "the lock retains the fixed in-container interpreter path"
        ),
    )
    parser.add_argument(
        "--route-bundle",
        type=Path,
        help="optional canonical production route bundle; emits authenticated launch-lock v2",
    )
    arguments = parser.parse_args(argv)
    route_bundle = None
    if arguments.route_bundle is not None:
        try:
            route_bundle = json.loads(arguments.route_bundle.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("production route bundle file is invalid") from error
        if not isinstance(route_bundle, dict):
            raise ValueError("production route bundle must be an object")
    lock = build_publication_worker_launch_lock(
        application_commit=arguments.application_commit,
        wheel=arguments.wheel,
        runtime_root=arguments.runtime_root,
        lock_path=arguments.lock,
        independent_digest_path=arguments.independent_digest,
        control_dir=arguments.control_dir,
        interpreter_source=arguments.interpreter_source,
        route_bundle=route_bundle,
    )
    print(lock.pin.source_manifest_sha256)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"publication worker launch lock rejected: {error}", file=sys.stderr)
        raise SystemExit(2) from None
