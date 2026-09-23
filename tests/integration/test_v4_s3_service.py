from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from secrets import token_hex
import json
import os
import subprocess
import time
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import pytest
import psycopg

from salience.contracts.storage import ObjectConflict, ObjectIntegrityError, ObjectNotFound, ObjectStoreNotQualified
from salience.storage.s3 import InMemoryArtifactRecorder, S3ObjectStore
from salience.storage.lifecycle import ObjectLifecycle


GARAGE_IMAGE = "dxflrs/garage@sha256:866bd13ed2038ba7e7190e840482bc27234c4afaf77be8cfa439ae088c1e4690"
SEAWEED_IMAGE = "chrislusf/seaweedfs@sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882"


@pytest.fixture(scope="module")
def garage_service(tmp_path_factory):
    directory = tmp_path_factory.mktemp("p0-garage")
    config = directory / "garage.toml"
    config.write_text(f'''metadata_dir = "/tmp/meta"
data_dir = "/tmp/data"
db_engine = "sqlite"
replication_factor = 1
rpc_bind_addr = "0.0.0.0:3901"
rpc_public_addr = "127.0.0.1:3901"
rpc_secret = "{token_hex(32)}"
[s3_api]
s3_region = "garage"
api_bind_addr = "0.0.0.0:3900"
''')
    config.chmod(0o600)
    access_key = "GK" + token_hex(16)
    secret_key = token_hex(32)
    container = subprocess.check_output(["docker", "run", "-d", "--rm", "--read-only", "--memory=512m", "--cpus=1", "--tmpfs", "/tmp:rw,size=256m", "-v", f"{config}:/etc/garage.toml:ro", "-e", f"GARAGE_DEFAULT_ACCESS_KEY={access_key}", "-e", f"GARAGE_DEFAULT_SECRET_KEY={secret_key}", "-e", "GARAGE_DEFAULT_BUCKET=p0-fixture", GARAGE_IMAGE, "/garage", "server", "--single-node", "--default-bucket"], text=True).strip()
    try:
        address = subprocess.check_output(["docker", "inspect", "-f", "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container], text=True).strip()
        client = boto3.client("s3", endpoint_url=f"http://{address}:3900", region_name="garage", aws_access_key_id=access_key, aws_secret_access_key=secret_key, config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}, s3={"addressing_style": "path"}))
        for attempt in range(30):
            try:
                if client.list_buckets()["Buckets"]:
                    break
            except (BotoCoreError, ClientError):
                pass
            time.sleep(1)
        else:
            pytest.fail("disposable pinned Garage did not become ready within 30 seconds")
        yield client
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=True, capture_output=True)


@pytest.fixture(scope="module")
def s3_service(tmp_path_factory):
    directory = tmp_path_factory.mktemp("p0-seaweed")
    config = directory / "s3.json"
    admin_key, admin_secret = token_hex(16), token_hex(32)
    writer_key, writer_secret = token_hex(16), token_hex(32)
    config.write_text(json.dumps({"identities": [
        {"name": "bootstrap", "credentials": [{"accessKey": admin_key, "secretKey": admin_secret}], "actions": ["Admin", "Read", "Write", "List", "Tagging"]},
        {"name": "isolated-writer", "credentials": [{"accessKey": writer_key, "secretKey": writer_secret}], "actions": ["Read:p0-fixture", "Write:p0-fixture", "List:p0-fixture"]},
    ]}))
    config.chmod(0o600)
    uid, gid = os.getuid(), os.getgid()
    container = subprocess.check_output(["docker", "run", "-d", "--rm", "--read-only", "--user", f"{uid}:{gid}", "--memory=512m", "--cpus=1", "--tmpfs", f"/data:rw,size=256m,uid={uid},gid={gid},mode=0700", "--tmpfs", "/tmp:rw,size=16m", "-v", f"{config}:/etc/s3.json:ro", SEAWEED_IMAGE, "server", "-dir=/data", "-ip=127.0.0.1", "-ip.bind=0.0.0.0", "-filer", "-s3", "-s3.config=/etc/s3.json", "-volume.max=1", "-master.volumeSizeLimitMB=16"], text=True).strip()
    try:
        address = subprocess.check_output(["docker", "inspect", "-f", "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container], text=True).strip()

        def client(access_key, secret_key):
            return boto3.client("s3", endpoint_url=f"http://{address}:8333", region_name="us-east-1", aws_access_key_id=access_key, aws_secret_access_key=secret_key, config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 0}, s3={"addressing_style": "path"}))

        assert subprocess.check_output(["docker", "exec", container, "id", "-u"], text=True).strip() == str(uid)
        subprocess.run(["docker", "exec", container, "test", "-r", "/etc/s3.json"], check=True)
        admin = client(admin_key, admin_secret)
        for attempt in range(45):
            try:
                try:
                    admin.create_bucket(Bucket="p0-fixture")
                except ClientError as error:
                    if error.response["Error"]["Code"] not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise
                admin.put_object(Bucket="p0-fixture", Key="__readiness__", Body=b"ready")
                response = admin.get_object(Bucket="p0-fixture", Key="__readiness__")
                try:
                    assert response["Body"].read() == b"ready"
                finally:
                    response["Body"].close()
                admin.delete_object(Bucket="p0-fixture", Key="__readiness__")
                break
            except (BotoCoreError, ClientError):
                time.sleep(1)
        else:
            pytest.fail("disposable pinned SeaweedFS did not become ready within 45 attempts")
        yield client(writer_key, writer_secret)
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=True, capture_output=True)


def test_real_s3_conditional_writes_readback_and_missing(s3_service):
    store = S3ObjectStore(client=s3_service, bucket="p0-fixture", artifact_recorder=InMemoryArtifactRecorder())
    store.qualify()
    values = {"key": "immutable", "data": b"original", "content_type": "text/plain", "metadata": {"classification": "internal"}}
    original = store.put(**values)
    assert store.put(**values) == original
    with pytest.raises(ObjectConflict):
        store.put(**(values | {"data": b"changed"}))
    assert store.get("immutable").data == b"original"
    with pytest.raises(ObjectNotFound):
        store.get("missing")
    s3_service.put_object(Bucket="p0-fixture", Key="immutable", Body=b"corrupted", Metadata=dict(original.metadata))
    with pytest.raises(ObjectIntegrityError):
        store.get("immutable")


def test_real_s3_concurrent_creation_has_one_winner(s3_service):
    store = S3ObjectStore(client=s3_service, bucket="p0-fixture", artifact_recorder=InMemoryArtifactRecorder())
    store.qualify()

    def create(data):
        try:
            store.put(key="race", data=data, content_type="text/plain", metadata={})
            return "success"
        except ObjectConflict:
            return "conflict"
        except ClientError as error:
            assert error.response["Error"]["Code"] in {"ConditionalRequestConflict", "409"}
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, [b"first", b"second"]))
    assert sorted(results) == ["conflict", "success"]


def test_pinned_garage_cannot_silently_enable_immutable_storage(garage_service):
    recorder = InMemoryArtifactRecorder()
    store = S3ObjectStore(client=garage_service, bucket="p0-fixture", artifact_recorder=recorder)
    with pytest.raises(ObjectStoreNotQualified, match="ignored conditional"):
        store.put(key="must-not-exist", data=b"protected", content_type="text/plain", metadata={})
    assert not recorder.artifacts
    with pytest.raises(ObjectNotFound):
        store.get("must-not-exist")


def test_real_s3_credential_is_bucket_scoped(s3_service):
    with pytest.raises(ClientError) as denied:
        s3_service.put_object(Bucket="foreign-workspace", Key="forbidden", Body=b"must-not-write")
    assert denied.value.response["Error"]["Code"] == "AccessDenied"


def test_real_s3_retention_and_missing_bytes_quarantine(s3_service):
    workspace = uuid4()
    database = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO workspaces (id,slug,display_name) VALUES (%s,%s,'real S3 retention')", (workspace, str(workspace)))
    store = S3ObjectStore(client=s3_service, bucket="p0-fixture", artifact_recorder=InMemoryArtifactRecorder())
    lifecycle = ObjectLifecycle(database_url=database, workspace_id=workspace, store=store)
    key = f"{workspace}/owned"
    receipt = store.put(key=key, data=b"verified", content_type="text/plain", metadata={})
    lifecycle.register(receipt, retain_until=datetime.now(timezone.utc) + timedelta(days=1))
    assert lifecycle.verify(key)
    assert not lifecycle.collect(key)
    lifecycle.reference(key, "test-owner")
    store.delete(key)
    assert not lifecycle.verify(key)
    with pytest.raises(PermissionError):
        lifecycle.reference(key, "late-consumer")
