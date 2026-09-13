import pytest

from salience.creative.media import C2paTool, MediaEngine, StorageCapacityGuard
from salience.storage.memory import MemoryObjectStore


@pytest.mark.asyncio
async def test_store_download_reuses_owned_bytes_by_sha256(tmp_path) -> None:
    engine = MediaEngine(
        object_store=MemoryObjectStore(),
        capacity_guard=StorageCapacityGuard(minimum_free_bytes=0),
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="missing-ffprobe",
        temporary_root=tmp_path,
    )

    first = await engine.store_download(b"fixture-media", media_type="video/mp4")
    second = await engine.store_download(b"fixture-media", media_type="video/mp4")

    assert first.content_hash == second.content_hash
    assert first.storage_key == second.storage_key
    assert first.byte_size == len(b"fixture-media")


def test_inspection_marks_missing_ffprobe_not_run(tmp_path) -> None:
    engine = MediaEngine(
        object_store=MemoryObjectStore(),
        capacity_guard=StorageCapacityGuard(minimum_free_bytes=0),
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="missing-ffprobe",
        temporary_root=tmp_path,
    )

    result = engine.inspect(tmp_path / "asset.mp4")

    assert (result.status, result.reason) == ("not_run", "ffprobe_unavailable")


def test_caption_rendering_and_c2pa_status_are_explicit(tmp_path) -> None:
    engine = MediaEngine(
        object_store=MemoryObjectStore(),
        capacity_guard=StorageCapacityGuard(minimum_free_bytes=0),
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="missing-ffprobe",
        temporary_root=tmp_path,
    )

    captions = engine.render_caption_track(
        [{"start_seconds": 0, "end_seconds": 1.2, "text": "Evidence linked."}]
    )
    c2pa = C2paTool().inspect(tmp_path / "asset.mp4")

    assert "WEBVTT" in captions
    assert (c2pa.status, c2pa.reason) == ("not_configured", "c2pa_tool_unconfigured")
