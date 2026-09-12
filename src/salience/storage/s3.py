from dataclasses import dataclass, field
from hashlib import sha256
from typing import Mapping, Protocol

from salience.contracts.storage import (
    ArtifactRecorder,
    ObjectNotFound,
    ObjectReceipt,
    StoredObject,
)


class S3Client(Protocol):
    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        Metadata: dict[str, str],
    ) -> object: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> object: ...


@dataclass
class InMemoryArtifactRecorder:
    artifacts: list["ArtifactRecord"] = field(default_factory=list)

    def record(self, receipt: ObjectReceipt) -> None:
        self.artifacts.append(
            ArtifactRecord(
                storage_key=receipt.key,
                content_hash=receipt.content_hash,
                byte_size=receipt.byte_size,
                content_type=receipt.content_type,
                metadata=dict(receipt.metadata),
            )
        )


@dataclass(frozen=True)
class ArtifactRecord:
    storage_key: str
    content_hash: str
    byte_size: int
    content_type: str
    metadata: Mapping[str, str]


class S3ObjectStore:
    def __init__(
        self,
        *,
        client: S3Client,
        bucket: str,
        artifact_recorder: ArtifactRecorder,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._artifact_recorder = artifact_recorder

    def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectReceipt:
        content_hash = sha256(data).hexdigest()
        object_metadata = {**metadata, "sha256": content_hash}
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata=object_metadata,
        )
        receipt = ObjectReceipt(
            key=key,
            content_hash=content_hash,
            byte_size=len(data),
            content_type=content_type,
            metadata=object_metadata,
        )
        self._artifact_recorder.record(receipt)
        return receipt

    def get(self, key: str) -> StoredObject:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as error:
            if error.__class__.__name__ in {"ClientError", "KeyError"}:
                raise ObjectNotFound(key) from error
            raise
        body = response["Body"]
        data = body.read()
        metadata = dict(response.get("Metadata", {}))
        return StoredObject(
            key=key,
            data=data,
            content_type=str(response.get("ContentType", "application/octet-stream")),
            metadata=metadata,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)
