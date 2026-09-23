from dataclasses import dataclass, field
from hashlib import sha256
from typing import Mapping, Protocol
from threading import Lock
from uuid import uuid4

from botocore.exceptions import ClientError

from salience.contracts.storage import (
    ArtifactRecorder,
    ObjectConflict,
    ObjectIntegrityError,
    ObjectStoreNotQualified,
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
        IfNoneMatch: str,
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
        max_object_bytes: int = 100_000_000,
    ) -> None:
        if max_object_bytes < 1:
            raise ValueError("object byte limit must be positive")
        self._max_object_bytes = max_object_bytes
        self._client = client
        self._bucket = bucket
        self._artifact_recorder = artifact_recorder
        self._qualified = False
        self._qualification_lock = Lock()

    def qualify(self) -> None:
        with self._qualification_lock:
            if self._qualified:
                return
            key = f"__salience_qualification__/{uuid4()}"
            first = b"a"
            try:
                self._client.put_object(Bucket=self._bucket, Key=key, Body=first, ContentType="application/octet-stream", Metadata={"sha256": sha256(first).hexdigest()}, IfNoneMatch="*")
                try:
                    self._client.put_object(Bucket=self._bucket, Key=key, Body=b"must-not-replace", ContentType="application/octet-stream", Metadata={}, IfNoneMatch="*")
                except ClientError as error:
                    if error.response.get("Error", {}).get("Code") not in {"PreconditionFailed", "412"}:
                        raise
                else:
                    raise ObjectStoreNotQualified("S3 endpoint ignored conditional create; writes disabled")
                if self.get(key).data != first:
                    raise ObjectStoreNotQualified("S3 conditional create did not retain original bytes")
            finally:
                self._client.delete_object(Bucket=self._bucket, Key=key)
            self._qualified = True

    def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectReceipt:
        if len(data) > self._max_object_bytes:
            raise ObjectIntegrityError("object exceeds configured byte limit")
        self.qualify()
        content_hash = sha256(data).hexdigest()
        object_metadata = {**metadata, "sha256": content_hash}
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=object_metadata,
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {"PreconditionFailed", "412"}:
                raise
        stored = self.get(key)
        if stored.data != data or stored.content_type != content_type or dict(stored.metadata) != object_metadata:
            raise ObjectConflict(key)
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
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                raise ObjectNotFound(key) from error
            raise
        except KeyError as error:
            raise ObjectNotFound(key) from error
        body = response["Body"]
        try:
            data = body.read(self._max_object_bytes + 1)
        finally:
            body.close()
        if len(data) > self._max_object_bytes:
            raise ObjectIntegrityError("object exceeds configured byte limit")
        metadata = dict(response.get("Metadata", {}))
        if sha256(data).hexdigest() != metadata.get("sha256"):
            raise ObjectIntegrityError(key)
        return StoredObject(
            key=key,
            data=data,
            content_type=str(response.get("ContentType", "application/octet-stream")),
            metadata=metadata,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)
