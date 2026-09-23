from hashlib import sha256
from typing import Mapping

from salience.contracts.storage import ObjectConflict, ObjectIntegrityError, ObjectNotFound, ObjectReceipt, StoredObject


class MemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[StoredObject, str]] = {}

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
        candidate = StoredObject(
            key=key,
            data=bytes(data),
            content_type=content_type,
            metadata=object_metadata,
        )
        existing = self._objects.setdefault(key, (candidate, content_hash))
        if existing != (candidate, content_hash):
            raise ObjectConflict(key)
        return ObjectReceipt(
            key=key,
            content_hash=content_hash,
            byte_size=len(data),
            content_type=content_type,
            metadata=dict(object_metadata),
        )

    def get(self, key: str) -> StoredObject:
        try:
            stored, expected_hash = self._objects[key]
        except KeyError as error:
            raise ObjectNotFound(key) from error
        if sha256(stored.data).hexdigest() != expected_hash:
            raise ObjectIntegrityError(key)
        return StoredObject(
            key=stored.key,
            data=bytes(stored.data),
            content_type=stored.content_type,
            metadata=dict(stored.metadata),
        )

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)
