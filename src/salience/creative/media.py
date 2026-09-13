"""Environment guards for future bounded creative media operations."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class StorageQuotaExceeded(RuntimeError):
    """Raised when a media operation would violate the free-space guard."""


class MediaToolUnavailable(RuntimeError):
    """Raised when a required locally managed media executable is unavailable."""


@dataclass(frozen=True, slots=True)
class StorageCapacityReceipt:
    free_bytes: int
    required_bytes: int
    minimum_free_bytes: int


class StorageCapacityGuard:
    def __init__(
        self,
        *,
        minimum_free_bytes: int,
        path: Path = Path("."),
        disk_usage: Callable[[str], tuple[int, int, int]] = shutil.disk_usage,
    ) -> None:
        if minimum_free_bytes <= 0:
            raise ValueError("minimum_free_bytes must be positive")
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
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._temporary_root = temporary_root

    def require_available(self, operation: str) -> str:
        executable = self._ffprobe_path if operation == "inspect" else self._ffmpeg_path
        resolved = self._resolve_executable(executable)
        if resolved is None:
            tool = "ffprobe" if operation == "inspect" else "ffmpeg"
            raise MediaToolUnavailable(f"{tool} is unavailable for {operation}")
        return resolved

    @staticmethod
    def _resolve_executable(value: str) -> str | None:
        path = Path(value)
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path)
        return shutil.which(value)
