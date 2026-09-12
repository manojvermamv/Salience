from hashlib import sha256
from typing import Mapping

from salience.contracts.storage import ObjectNotFound, ObjectReceipt, StoredObject


class MemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, StoredObject] = {}

    def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectReceipt:
        content_hash = sha256(data).hexdigest()
        object_metadata = dict(metadata)
        self._objects[key] = StoredObject(
            key=key,
            data=bytes(data),
            content_type=content_type,
            metadata=object_metadata,
        )
        return ObjectReceipt(
            key=key,
            content_hash=content_hash,
            byte_size=len(data),
            content_type=content_type,
            metadata=object_metadata,
        )

    def get(self, key: str) -> StoredObject:
        try:
            stored = self._objects[key]
        except KeyError as error:
            raise ObjectNotFound(key) from error
        return StoredObject(
            key=stored.key,
            data=bytes(stored.data),
            content_type=stored.content_type,
            metadata=dict(stored.metadata),
        )

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)

