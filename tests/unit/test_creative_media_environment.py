import pytest


def test_storage_capacity_guard_rejects_media_below_minimum_free_space() -> None:
    from salience.creative.media import StorageCapacityGuard, StorageQuotaExceeded

    guard = StorageCapacityGuard(
        minimum_free_bytes=100,
        disk_usage=lambda _: (1000, 950, 50),
    )

    with pytest.raises(StorageQuotaExceeded, match="minimum free space"):
        guard.require_capacity(required_bytes=1)


def test_media_engine_reports_missing_ffprobe_without_running_a_command(tmp_path) -> None:
    from salience.creative.media import MediaEngine, MediaToolUnavailable

    engine = MediaEngine(
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="missing-ffprobe",
        temporary_root=tmp_path,
    )

    with pytest.raises(MediaToolUnavailable, match="ffprobe"):
        engine.require_available("inspect")
