from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO

import pytest

from salience.contracts.storage import ObjectNotFound, ObjectStore
from salience.storage.memory import MemoryObjectStore
from salience.storage.s3 import InMemoryArtifactRecorder, S3ObjectStore


@dataclass
class FakeS3Object:
    data: bytes
    content_type: str
    metadata: dict[str, str]


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], FakeS3Object] = {}

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        Metadata: dict[str, str],
    ) -> None:
        self.objects[(Bucket, Key)] = FakeS3Object(Body, ContentType, Metadata)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        try:
            stored = self.objects[(Bucket, Key)]
        except KeyError as error:
            raise KeyError("NoSuchKey") from error
        return {
            "Body": BytesIO(stored.data),
            "ContentType": stored.content_type,
            "Metadata": stored.metadata,
        }

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


def object_store_contract(store: ObjectStore) -> None:
    receipt = store.put(
        key="a.txt",
        data=b"phase-1",
        content_type="text/plain",
        metadata={"data_classification": "internal"},
    )

    assert receipt.key == "a.txt"
    assert receipt.byte_size == len(b"phase-1")
    assert receipt.content_hash
    fetched = store.get(receipt.key)
    assert fetched.data == b"phase-1"
    assert fetched.content_type == "text/plain"
    assert fetched.metadata["data_classification"] == "internal"

    store.delete(receipt.key)
    with pytest.raises(ObjectNotFound):
        store.get(receipt.key)


def test_memory_object_store_contract() -> None:
    object_store_contract(MemoryObjectStore())


def test_s3_object_store_contract_and_records_owned_metadata() -> None:
    client = FakeS3Client()
    recorder = InMemoryArtifactRecorder()
    store = S3ObjectStore(client=client, bucket="salience", artifact_recorder=recorder)

    object_store_contract(store)

    receipt = store.put(
        key="recorded.txt",
        data=b"owned",
        content_type="text/plain",
        metadata={"data_classification": "internal"},
    )
    assert client.objects[("salience", receipt.key)].metadata["sha256"] == receipt.content_hash
    assert recorder.artifacts[-1].storage_key == receipt.key

