"""Bounded media ownership, inspection, composition, caption, and C2PA boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence

from salience.contracts.storage import ObjectStore


class StorageQuotaExceeded(RuntimeError):
    """Raised when a media operation would violate the free-space guard."""


class MediaToolUnavailable(RuntimeError):
    """Raised when a required locally managed media executable is unavailable."""


class MediaImportError(RuntimeError):
    """Raised when media bytes cannot be safely imported into owned storage."""


@dataclass(frozen=True, slots=True)
class StorageCapacityReceipt:
    free_bytes: int
    required_bytes: int
    minimum_free_bytes: int


@dataclass(frozen=True, slots=True)
class MediaStorageReceipt:
    storage_key: str
    content_hash: str
    byte_size: int
    media_type: str


@dataclass(frozen=True, slots=True)
class MediaInspection:
    status: str
    reason: str | None
    properties: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MediaOperationReceipt:
    status: str
    reason: str | None
    output_path: Path


@dataclass(frozen=True, slots=True)
class C2paValidation:
    status: str
    reason: str | None
    manifest_reference: str | None = None


class StorageCapacityGuard:
    def __init__(
        self,
        *,
        minimum_free_bytes: int,
        path: Path = Path("."),
        disk_usage: Callable[[str], tuple[int, int, int]] = shutil.disk_usage,
    ) -> None:
        if minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must not be negative")
        self._minimum_free_bytes = minimum_free_bytes
        self._path = path
        self._disk_usage = disk_usage

    def require_capacity(self, *, required_bytes: int) -> StorageCapacityReceipt:
        if required_bytes < 0:
            raise ValueError("required_bytes must not be negative")
        free_bytes = self._disk_usage(str(self._path))[2]
        if free_bytes - required_bytes < self._minimum_free_bytes:
            raise StorageQuotaExceeded(
                "minimum free space would be violated by creative media operation"
            )
        return StorageCapacityReceipt(
            free_bytes=free_bytes,
            required_bytes=required_bytes,
            minimum_free_bytes=self._minimum_free_bytes,
        )


class MediaEngine:
    def __init__(
        self,
        *,
        ffmpeg_path: str,
        ffprobe_path: str,
        temporary_root: Path,
        object_store: ObjectStore | None = None,
        capacity_guard: StorageCapacityGuard | None = None,
        maximum_storage_bytes: int | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if maximum_storage_bytes is not None and maximum_storage_bytes <= 0:
            raise ValueError("maximum_storage_bytes must be positive")
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._temporary_root = temporary_root
        self._object_store = object_store
        self._capacity_guard = capacity_guard
        self._maximum_storage_bytes = maximum_storage_bytes
        self._command_runner = command_runner

    def require_available(self, operation: str) -> str:
        executable = self._ffprobe_path if operation == "inspect" else self._ffmpeg_path
        resolved = self._resolve_executable(executable)
        if resolved is None:
            tool = "ffprobe" if operation == "inspect" else "ffmpeg"
            raise MediaToolUnavailable(f"{tool} is unavailable for {operation}")
        return resolved

    async def store_download(self, data: bytes, *, media_type: str) -> MediaStorageReceipt:
        if self._object_store is None or self._capacity_guard is None:
            raise MediaImportError("media import requires an object store and capacity guard")
        if not isinstance(data, bytes) or not data:
            raise MediaImportError("media import requires non-empty bytes")
        if not media_type or "/" not in media_type:
            raise MediaImportError("media import requires a MIME media type")
        if self._maximum_storage_bytes is not None and len(data) > self._maximum_storage_bytes:
            raise StorageQuotaExceeded("creative media exceeds configured storage limit")
        self._capacity_guard.require_capacity(required_bytes=len(data))
        temporary_path = self._write_temporary_bytes(data)
        try:
            content_hash = _hash_file(temporary_path)
            storage_key = f"creative/assets/{content_hash}.{_suffix_for(media_type)}"
            receipt = self._object_store.put(
                key=storage_key,
                data=temporary_path.read_bytes(),
                content_type=media_type,
                metadata={"data_classification": "internal", "sha256": content_hash},
            )
            if receipt.content_hash != content_hash:
                raise MediaImportError("object store returned a mismatched content hash")
            return MediaStorageReceipt(
                storage_key=receipt.key,
                content_hash=content_hash,
                byte_size=receipt.byte_size,
                media_type=receipt.content_type,
            )
        finally:
            temporary_path.unlink(missing_ok=True)

    def inspect(self, asset_path: Path, *, timeout_seconds: int = 30) -> MediaInspection:
        try:
            ffprobe = self.require_available("inspect")
        except MediaToolUnavailable:
            return MediaInspection("not_run", "ffprobe_unavailable", {})
        result = self._command_runner(
            [
                ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(asset_path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            return MediaInspection("invalid", "ffprobe_failed", {})
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return MediaInspection("invalid", "ffprobe_invalid_json", {})
        if not isinstance(payload, dict):
            return MediaInspection("invalid", "ffprobe_invalid_payload", {})
        return MediaInspection("valid", None, _inspection_properties(payload))

    def inspect_bytes(
        self, data: bytes, *, timeout_seconds: int = 30
    ) -> MediaInspection:
        if not isinstance(data, bytes) or not data:
            return MediaInspection("invalid", "empty_media", {})
        temporary_path = self._write_temporary_bytes(data)
        try:
            return self.inspect(temporary_path, timeout_seconds=timeout_seconds)
        finally:
            temporary_path.unlink(missing_ok=True)

    def compose(
        self,
        input_paths: Sequence[Path],
        output_path: Path,
        *,
        timeout_seconds: int = 60,
    ) -> MediaOperationReceipt:
        try:
            ffmpeg = self.require_available("compose")
        except MediaToolUnavailable:
            return MediaOperationReceipt("not_run", "ffmpeg_unavailable", output_path)
        root = self._temporary_root.resolve()
        resolved_output = output_path.resolve()
        resolved_inputs = [input_path.resolve() for input_path in input_paths]
        if not resolved_inputs or any(not _within(root, input_path) for input_path in resolved_inputs):
            return MediaOperationReceipt("invalid", "input_outside_temporary_root", output_path)
        if not _within(root, resolved_output):
            return MediaOperationReceipt("invalid", "output_outside_temporary_root", output_path)
        command = [ffmpeg, "-y"]
        for input_path in resolved_inputs:
            command.extend(("-i", str(input_path)))
        command.extend(("-c", "copy", str(resolved_output)))
        result = self._command_runner(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        return MediaOperationReceipt(
            "completed" if result.returncode == 0 else "failed",
            None if result.returncode == 0 else "ffmpeg_failed",
            output_path,
        )

    def render_caption_track(self, cues: Sequence[Mapping[str, object]]) -> str:
        rendered = ["WEBVTT", ""]
        for index, cue in enumerate(cues, start=1):
            start = cue.get("start_seconds")
            end = cue.get("end_seconds")
            text = cue.get("text")
            if (
                not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or end <= start
                or not isinstance(text, str)
                or not text.strip()
            ):
                raise ValueError("caption cue must have an ordered time range and non-empty text")
            rendered.extend((str(index), f"{_vtt_time(start)} --> {_vtt_time(end)}", text.strip(), ""))
        return "\n".join(rendered)

    def _write_temporary_bytes(self, data: bytes) -> Path:
        self._temporary_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_root = self._temporary_root.resolve()
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="import-", suffix=".media", dir=temporary_root, delete=False
        ) as temporary_file:
            temporary_file.write(data)
            return Path(temporary_file.name)

    @staticmethod
    def _resolve_executable(value: str) -> str | None:
        path = Path(value)
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path)
        return shutil.which(value)


class C2paTool:
    """Read-only C2PA status boundary; signing remains operator-configured future work."""

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable

    def inspect(self, asset_path: Path) -> C2paValidation:
        if self._executable is None:
            return C2paValidation("not_configured", "c2pa_tool_unconfigured")
        if MediaEngine._resolve_executable(self._executable) is None:
            return C2paValidation("unavailable", "c2pa_tool_unavailable")
        return C2paValidation("not_configured", "c2pa_validation_not_configured")


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as asset_file:
        for chunk in iter(lambda: asset_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _suffix_for(media_type: str) -> str:
    return {
        "video/mp4": "mp4",
        "video/webm": "webm",
        "image/jpeg": "jpg",
        "image/png": "png",
        "audio/mpeg": "mp3",
    }.get(media_type, "bin")


def _inspection_properties(payload: Mapping[str, Any]) -> dict[str, object]:
    format_data = payload.get("format")
    streams = payload.get("streams")
    if not isinstance(format_data, Mapping):
        format_data = {}
    if not isinstance(streams, list):
        streams = []
    video_stream = next(
        (stream for stream in streams if isinstance(stream, Mapping) and stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in streams if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"),
        {},
    )
    return {
        "container": format_data.get("format_name"),
        "duration_seconds": _float_or_none(format_data.get("duration")),
        "byte_size": _integer_or_none(format_data.get("size")),
        "video_codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "frame_rate": video_stream.get("avg_frame_rate"),
        "audio_codec": audio_stream.get("codec_name"),
        "audio_sample_rate": _integer_or_none(audio_stream.get("sample_rate")),
    }


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _vtt_time(value: int | float) -> str:
    milliseconds = round(float(value) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
