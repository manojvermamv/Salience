from dataclasses import replace

from botocore.exceptions import ClientError
import pytest

from salience.contracts.storage import ObjectConflict, ObjectIntegrityError, ObjectNotFound
from salience.storage.memory import MemoryObjectStore
from salience.storage.s3 import InMemoryArtifactRecorder, S3ObjectStore
from tests.contracts.test_object_store import FakeS3Client


@pytest.mark.parametrize("kind", ["memory", "s3"])
def test_immutable_key_allows_exact_retry_only(kind):
    store = MemoryObjectStore() if kind == "memory" else S3ObjectStore(client=FakeS3Client(), bucket="p0", artifact_recorder=InMemoryArtifactRecorder())
    values = {"key": "immutable", "data": b"original", "content_type": "text/plain", "metadata": {"classification": "internal"}}
    assert store.put(**values) == store.put(**values)
    with pytest.raises(ObjectConflict):
        store.put(**(values | {"data": b"changed"}))
    with pytest.raises(ObjectConflict):
        store.put(**(values | {"metadata": {"classification": "public"}}))
    assert store.get("immutable").data == b"original"


def test_corrupt_readback_never_records_ready_metadata():
    client = FakeS3Client()
    recorder = InMemoryArtifactRecorder()
    store = S3ObjectStore(client=client, bucket="p0", artifact_recorder=recorder)
    store.put(key="asset", data=b"original", content_type="text/plain", metadata={})
    client.objects[("p0", "asset")] = replace(client.objects[("p0", "asset")], data=b"corrupt")
    with pytest.raises(ObjectIntegrityError):
        store.get("asset")
    with pytest.raises(ObjectIntegrityError):
        store.put(key="asset", data=b"original", content_type="text/plain", metadata={})
    assert len(recorder.artifacts) == 1


def test_access_errors_are_not_disguised_as_missing_objects():
    class DeniedClient(FakeS3Client):
        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    store = S3ObjectStore(client=DeniedClient(), bucket="p0", artifact_recorder=InMemoryArtifactRecorder())
    with pytest.raises(ClientError):
        store.get("asset")
    with pytest.raises(ObjectNotFound):
        S3ObjectStore(client=FakeS3Client(), bucket="p0", artifact_recorder=InMemoryArtifactRecorder()).get("absent")


def test_s3_byte_limit_applies_before_upload_and_during_readback():
    client = FakeS3Client()
    recorder = InMemoryArtifactRecorder()
    store = S3ObjectStore(client=client, bucket="p0", artifact_recorder=recorder, max_object_bytes=4)
    with pytest.raises(ObjectIntegrityError, match="byte limit"):
        store.put(key="oversized", data=b"too-large", content_type="text/plain", metadata={})
    assert not client.objects
    store.put(key="small", data=b"safe", content_type="text/plain", metadata={})
    client.objects[("p0", "small")] = replace(client.objects[("p0", "small")], data=b"larger-than-allowed")
    with pytest.raises(ObjectIntegrityError, match="byte limit"):
        store.get("small")
