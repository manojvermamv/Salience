from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import psycopg
import pytest

from salience.contracts.storage import ObjectIntegrityError, ObjectNotFound
from salience.storage.lifecycle import ObjectLifecycle
from salience.storage.memory import MemoryObjectStore


def test_quarantine_references_and_retention_control_orphan_cleanup():
    database = os.environ["TEST_DATABASE_URL"]
    workspace = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO workspaces (id, slug, display_name) VALUES (%s,%s,'retention')", (workspace, str(workspace)))
    store = MemoryObjectStore()
    lifecycle = ObjectLifecycle(database_url=database, workspace_id=workspace, store=store)
    key = str(workspace) + "/asset"
    receipt = store.put(key=key, data=b"retained", content_type="text/plain", metadata={})
    now = datetime.now(timezone.utc)
    lifecycle.register(receipt, retain_until=now + timedelta(days=1))
    assert lifecycle.verify(key)
    assert not lifecycle.collect(key)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE object_inventory SET retain_until=now() - interval '1 second' WHERE storage_key=%s", (key,))
    lifecycle.reference(key, "fixture-owner")
    assert not lifecycle.collect(key)
    lifecycle.release(key, "fixture-owner")
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE object_inventory SET legal_hold=true WHERE storage_key=%s", (key,))
    assert not lifecycle.collect(key)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE object_inventory SET legal_hold=false WHERE storage_key=%s", (key,))
    assert lifecycle.collect(key)
    assert lifecycle.collect(key)
    with pytest.raises(ObjectNotFound):
        store.get(key)
    with pytest.raises(PermissionError):
        lifecycle.reference(key, "late-owner")


def test_missing_and_corrupt_bytes_persist_quarantine():
    database = os.environ["TEST_DATABASE_URL"]
    workspace = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO workspaces (id, slug, display_name) VALUES (%s,%s,'quarantine')", (workspace, str(workspace)))
    store = MemoryObjectStore()
    lifecycle = ObjectLifecycle(database_url=database, workspace_id=workspace, store=store)
    key = str(workspace) + "/missing"
    receipt = store.put(key=key, data=b"original", content_type="text/plain", metadata={})
    lifecycle.register(receipt, retain_until=datetime.now(timezone.utc))
    store.delete(key)
    assert not lifecycle.verify(key)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state FROM object_inventory WHERE storage_key=%s", (key,)).fetchone()[0] == "quarantined"
    with pytest.raises(PermissionError):
        lifecycle.reference(key, "consumer")
    store.put(key=key, data=b"different", content_type="text/plain", metadata={})
    assert not lifecycle.verify(key)


def test_crash_after_delete_cannot_create_a_dangling_reference():
    database = os.environ["TEST_DATABASE_URL"]
    workspace = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO workspaces (id,slug,display_name) VALUES (%s,%s,'crash')", (workspace, str(workspace)))

    class InterruptedStore(MemoryObjectStore):
        def delete(self, key):
            super().delete(key)
            raise RuntimeError("simulated failure after object delete before database commit")

    store = InterruptedStore()
    lifecycle = ObjectLifecycle(database_url=database, workspace_id=workspace, store=store)
    key = str(workspace) + "/orphan"
    receipt = store.put(key=key, data=b"owned", content_type="text/plain", metadata={})
    lifecycle.register(receipt, retain_until=datetime.now(timezone.utc) - timedelta(days=1))
    assert lifecycle.verify(key)
    with pytest.raises(RuntimeError):
        lifecycle.collect(key)
    with pytest.raises(PermissionError, match="readback"):
        lifecycle.reference(key, "racing-owner")
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state FROM object_inventory WHERE storage_key=%s", (key,)).fetchone()[0] == "quarantined"
        assert connection.execute("SELECT count(*) FROM object_references WHERE storage_key=%s", (key,)).fetchone()[0] == 0
