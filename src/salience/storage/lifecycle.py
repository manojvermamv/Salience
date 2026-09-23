from datetime import datetime
from hashlib import sha256
from uuid import UUID

import psycopg

from salience.contracts.storage import ObjectConflict, ObjectIntegrityError, ObjectNotFound, ObjectReceipt, ObjectStore


class ObjectLifecycle:
    def __init__(self, *, database_url: str, workspace_id: UUID, store: ObjectStore):
        self.database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self.workspace_id = workspace_id
        self.store = store

    def _connection(self):
        return psycopg.connect(self.database_url, connect_timeout=3, options="-c statement_timeout=3000 -c lock_timeout=3000")

    def _lock(self, connection, key):
        row = connection.execute("SELECT content_hash, byte_size, content_type, state, retain_until > now(), legal_hold FROM object_inventory WHERE workspace_id=%s AND storage_key=%s FOR UPDATE", (self.workspace_id, key)).fetchone()
        if not row:
            raise ObjectNotFound(key)
        return row

    def register(self, receipt: ObjectReceipt, *, retain_until: datetime, legal_hold: bool = False) -> None:
        if not receipt.key.startswith(str(self.workspace_id) + "/") or retain_until.tzinfo is None:
            raise ValueError("workspace-prefixed key and timezone-aware retention are required")
        with self._connection() as connection:
            connection.execute("INSERT INTO object_inventory (storage_key,workspace_id,content_hash,byte_size,content_type,retain_until,legal_hold) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (storage_key) DO NOTHING", (receipt.key, self.workspace_id, receipt.content_hash, receipt.byte_size, receipt.content_type, retain_until, legal_hold))
            row = self._lock(connection, receipt.key)
            if row[:3] != (receipt.content_hash, receipt.byte_size, receipt.content_type) or row[3] == "deleted":
                raise ObjectConflict(receipt.key)
            connection.execute("UPDATE object_inventory SET retain_until=GREATEST(retain_until,%s), legal_hold=legal_hold OR %s WHERE storage_key=%s", (retain_until, legal_hold, receipt.key))

    def verify(self, key: str) -> bool:
        with self._connection() as connection:
            row = self._lock(connection, key)
            if row[3] == "deleted":
                return False
            try:
                stored = self.store.get(key)
                valid = (sha256(stored.data).hexdigest(), len(stored.data), stored.content_type) == row[:3]
            except (ObjectNotFound, ObjectIntegrityError):
                valid = False
            connection.execute("UPDATE object_inventory SET state=%s WHERE storage_key=%s", ("verified" if valid else "quarantined", key))
            return valid

    def reference(self, key: str, owner_id: str) -> None:
        with self._connection() as connection:
            row = self._lock(connection, key)
            if row[3] != "verified":
                raise PermissionError("unverified or quarantined object cannot gain a ready reference")
            try:
                stored = self.store.get(key)
                valid = (sha256(stored.data).hexdigest(), len(stored.data), stored.content_type) == row[:3]
            except (ObjectNotFound, ObjectIntegrityError):
                valid = False
            if valid:
                connection.execute("INSERT INTO object_references (workspace_id,storage_key,owner_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (self.workspace_id, key, owner_id))
            else:
                connection.execute("UPDATE object_inventory SET state='quarantined' WHERE storage_key=%s", (key,))
        if not valid:
            raise PermissionError("object readback failed before reference")

    def release(self, key: str, owner_id: str) -> None:
        with self._connection() as connection:
            self._lock(connection, key)
            connection.execute("DELETE FROM object_references WHERE workspace_id=%s AND storage_key=%s AND owner_id=%s", (self.workspace_id, key, owner_id))

    def collect(self, key: str) -> bool:
        with self._connection() as connection:
            row = self._lock(connection, key)
            if row[3] == "deleted":
                return True
            references = connection.execute("SELECT EXISTS (SELECT 1 FROM object_references WHERE storage_key=%s) OR EXISTS (SELECT 1 FROM artifacts WHERE storage_key=%s)", (key, key)).fetchone()[0]
            if row[4] or row[5] or references:
                return False
            self.store.delete(key)
            try:
                self.store.get(key)
            except ObjectNotFound:
                connection.execute("UPDATE object_inventory SET state='deleted' WHERE storage_key=%s", (key,))
                return True
            raise ObjectIntegrityError("delete has not been confirmed")
