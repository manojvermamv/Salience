"""Fail-closed prerequisites for exact Task 8 candidate-image tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


_CONTROL_FILES = {
    "launch-v4-publication-worker.py",
    "publication-worker-launch-lock.json",
    "publication-worker-launch-lock.sha256",
}
_CANDIDATE_FILES = (
    "application-commit.txt",
    "image-attestation.json",
    "worker-image-id.txt",
    "salience.whl",
    "publication-worker-launch-lock.sha256",
)
_PROGRESS_FILE = "worker-qualification-progress.json"
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular_bytes(path: Path) -> bytes:
    metadata = path.lstat()
    assert stat.S_ISREG(metadata.st_mode) and not path.is_symlink(), (
        f"candidate file must be regular: {path}"
    )
    return path.read_bytes()


def _runtime_manifest(runtime_root: Path) -> dict[str, object]:
    files: dict[str, str] = {}
    pyc_files: list[str] = []
    for current, directories, names in os.walk(runtime_root, followlinks=False):
        for name in (*directories, *names):
            path = Path(current) / name
            relative = path.relative_to(runtime_root).as_posix()
            if path.is_symlink():
                files[relative] = "symlink:" + os.readlink(path)
                if path.name.endswith(".pyc") or path.resolve(strict=False).suffix == ".pyc":
                    pyc_files.append(relative)
            elif path.is_dir():
                files[relative] = "directory"
            else:
                metadata = path.stat()
                if not stat.S_ISREG(metadata.st_mode):
                    files[relative] = "non-regular"
                else:
                    files[relative] = "sha256:" + _sha256(path.read_bytes())
                if path.name.endswith(".pyc"):
                    pyc_files.append(relative)
    return {"files": files, "pyc_files": sorted(pyc_files)}


def _docker_image_id(image_ref: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image_ref],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def load_candidate_state(
    *,
    candidate_root: Path,
    tracked_lock_path: Path,
    tracked_launcher_path: Path,
    runtime_prefix: Path,
    image_inspector=None,
) -> dict[str, object] | None:
    """Validate the built image bundle, or return None when it is wholly absent.

    Qualification progress is produced by the Task 8 run; it is optional during
    candidate construction, but is identity-checked whenever it already exists.
    """
    candidate_root = Path(candidate_root)
    control_bundle = candidate_root / "control-bundle"
    tracked_lock_path = Path(tracked_lock_path)
    tracked_launcher_path = Path(tracked_launcher_path)
    runtime_prefix = Path(runtime_prefix)
    assert not candidate_root.is_symlink(), "candidate root must not be a symlink"
    assert not control_bundle.is_symlink(), "candidate control bundle must not be a symlink"
    if candidate_root.exists():
        assert candidate_root.is_dir(), "candidate root must be a directory"
    if control_bundle.exists():
        assert control_bundle.is_dir(), "candidate control bundle must be a directory"

    candidate_paths = [candidate_root / name for name in _CANDIDATE_FILES]
    progress_path = candidate_root / _PROGRESS_FILE
    control_paths = [control_bundle / name for name in sorted(_CONTROL_FILES)]
    runtime_hint = runtime_prefix / "usr/local/lib/python3.13/site-packages"
    markers = [*candidate_paths, *control_paths, runtime_hint]
    present = [path for path in (*markers, progress_path) if path.exists() or path.is_symlink()]
    if not present:
        return None

    missing = [path for path in markers if not path.exists() and not path.is_symlink()]
    if missing:
        labels = [str(path.relative_to(candidate_root)) if candidate_root in path.parents else str(path) for path in missing]
        raise AssertionError(
            "partial Task 8 candidate state: missing " + ", ".join(labels)
        )
    assert {path.name for path in control_bundle.iterdir()} == _CONTROL_FILES, (
        "candidate control bundle must contain exactly the fixed launcher and lock files"
    )

    candidate_bytes = {path.name: _regular_bytes(path) for path in candidate_paths}
    progress = None
    if progress_path.exists() or progress_path.is_symlink():
        progress = json.loads(_regular_bytes(progress_path))
    control_bytes = {path.name: _regular_bytes(path) for path in control_paths}
    tracked_lock_bytes = _regular_bytes(tracked_lock_path)
    tracked_launcher_bytes = _regular_bytes(tracked_launcher_path)
    assert control_bytes["publication-worker-launch-lock.json"] == tracked_lock_bytes, (
        "candidate launch lock differs from the tracked lock"
    )
    assert control_bytes["launch-v4-publication-worker.py"] == tracked_launcher_bytes, (
        "candidate launcher differs from the tracked launcher"
    )

    lock = json.loads(tracked_lock_bytes)
    assert tracked_lock_bytes == (
        json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    lock_digest = _sha256(tracked_lock_bytes)
    assert control_bytes["publication-worker-launch-lock.sha256"] == (
        lock_digest + "\n"
    ).encode()
    assert candidate_bytes["publication-worker-launch-lock.sha256"] == (
        lock_digest + "\n"
    ).encode()
    pin = lock.get("pin")
    assert isinstance(pin, dict), "tracked candidate launch lock pin is malformed"

    attestation = json.loads(candidate_bytes["image-attestation.json"])
    image_id = candidate_bytes["worker-image-id.txt"].decode("ascii").strip()
    attested_image_id = attestation.get("image_id")
    assert _IMAGE_ID.fullmatch(image_id) and _IMAGE_ID.fullmatch(
        str(attested_image_id)
    ), "candidate image ID is malformed"
    assert image_id == attested_image_id, "candidate image ID disagrees with its attestation"

    application_commit = pin.get("application_commit")
    image_ref = attestation.get("image_ref")
    runtime_root = lock.get("runtime_root")
    runtime_manifest_sha256 = attestation.get("runtime_manifest_sha256")
    assert isinstance(application_commit, str) and re.fullmatch(
        r"[0-9a-f]{40}", application_commit
    ), "tracked application commit pin is malformed"
    recorded_application_commit = candidate_bytes["application-commit.txt"]
    assert recorded_application_commit == (application_commit + "\n").encode("ascii"), (
        "candidate application-commit marker disagrees with the tracked launch lock"
    )
    assert image_ref == f"salience-p1-floor-{application_commit[:7]}-worker:latest", (
        "candidate image reference does not identify the tracked application commit"
    )
    assert runtime_root == "/usr/local/lib/python3.13/site-packages"
    assert _SHA256.fullmatch(str(runtime_manifest_sha256)), (
        "candidate runtime manifest digest is malformed"
    )
    for field, expected in (
        ("application_commit", application_commit),
        ("image_ref", image_ref),
        ("image_id", image_id),
        ("runtime_root", runtime_root),
        ("runtime_manifest_sha256", runtime_manifest_sha256),
    ):
        assert attestation.get(field) == expected, f"candidate attestation {field} mismatch"
        if progress is not None:
            assert progress.get(field) == expected, f"candidate progress {field} mismatch"
    assert attestation.get("wheel_sha256") == pin.get("artifact_sha256")
    assert attestation.get("record_sha256") == pin.get("distribution_record_sha256")
    assert attestation.get("record_ok") is True
    assert attestation.get("pyc_files") == []
    assert attestation.get("source_tree_loaded") is False
    if progress is not None:
        assert progress.get("launch_lock_sha256") == lock_digest
        assert progress.get("launcher_sha256") == lock.get("launcher_sha256")
        assert progress.get("interpreter") == lock.get("interpreter_path")
        assert progress.get("interpreter_sha256") == lock.get("interpreter_sha256")
        assert progress.get("wheel_sha256") == pin.get("artifact_sha256")
        assert progress.get("record_sha256") == pin.get("distribution_record_sha256")
        assert progress.get("source_manifest_sha256") == pin.get("source_manifest_sha256")
        assert progress.get("source_files") == pin.get("source_files")
        assert progress.get("runtime_imports") == pin.get("runtime_imports")
        assert progress.get("source_tree_loaded") is False
        assert progress.get("effects_enabled") is False
        assert progress.get("production_effects_enabled") is False
        assert progress.get("fixture_publication_budget") == 0
    assert _sha256(candidate_bytes["salience.whl"]) == pin.get("artifact_sha256"), (
        "candidate wheel differs from the tracked launch lock"
    )

    if image_inspector is None:
        image_inspector = _docker_image_id
    observed_image_id = image_inspector(image_ref)
    assert observed_image_id == image_id, "candidate image ID disagrees with Docker image inspect"

    assert runtime_hint.is_dir() and not runtime_hint.is_symlink(), (
        "candidate extracted runtime root is absent or unsafe"
    )
    manifest = _runtime_manifest(runtime_hint)
    manifest_digest = _sha256(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    assert manifest_digest == runtime_manifest_sha256, (
        "candidate extracted runtime differs from its image attestation"
    )

    return {
        "candidate_root": candidate_root,
        "control_bundle": control_bundle,
        "image_ref": image_ref,
        "image_id": image_id,
        "application_commit": application_commit,
        "runtime_root": runtime_root,
        "runtime_path": runtime_hint,
        "runtime_manifest_sha256": manifest_digest,
        "lock": lock,
        "attestation": attestation,
        "progress": progress,
    }
