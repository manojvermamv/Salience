import pytest

from salience.creative.media import MediaEngine, StorageCapacityGuard, StorageQuotaExceeded
from salience.storage.memory import MemoryObjectStore


@pytest.mark.asyncio
async def test_guarded_media_import_writes_only_owned_metadata_and_no_temp_file(tmp_path) -> None:
    store = MemoryObjectStore()
    engine = MediaEngine(
        object_store=store,
        capacity_guard=StorageCapacityGuard(minimum_free_bytes=0),
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="missing-ffprobe",
        temporary_root=tmp_path,
    )

    receipt = await engine.store_download(b"fixture-media", media_type="video/mp4")
    stored = store.get(receipt.storage_key)

    assert stored.metadata == {"data_classification": "internal", "sha256": receipt.content_hash}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_guarded_media_import_checks_capacity_before_writing(tmp_path) -> None:
    engine = MediaEngine(
        object_store=MemoryObjectStore(),
        capacity_guard=StorageCapacityGuard(
            minimum_free_bytes=10, disk_usage=lambda _: (100, 95, 5)
        ),
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="missing-ffprobe",
        temporary_root=tmp_path,
    )

    with pytest.raises(StorageQuotaExceeded):
        await engine.store_download(b"fixture-media", media_type="video/mp4")
    assert list(tmp_path.iterdir()) == []
