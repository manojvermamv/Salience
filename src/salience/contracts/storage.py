from dataclasses import dataclass
from typing import Mapping, Protocol


class ObjectNotFound(KeyError):
    pass


@dataclass(frozen=True)
class ObjectReceipt:
    key: str
    content_hash: str
    byte_size: int
    content_type: str
    metadata: Mapping[str, str]


@dataclass(frozen=True)
class StoredObject:
    key: str
    data: bytes
    content_type: str
    metadata: Mapping[str, str]


class ObjectStore(Protocol):
    def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectReceipt: ...

    def get(self, key: str) -> StoredObject: ...

    def delete(self, key: str) -> None: ...


class ArtifactRecorder(Protocol):
    def record(self, receipt: ObjectReceipt) -> None: ...

