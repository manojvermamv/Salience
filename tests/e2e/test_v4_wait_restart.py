"""Kill a timer worker, restore its DB snapshot, restart Temporal and redeliver."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from temporalio.client import Client
from temporalio.worker import Replayer

from test_v4_outbox_recovery import scenario
from salience.cycles.governance import CycleGovernance
from salience.cycles.runtime_waits import RuntimeWaits, FixtureWaitDriver
from salience.cycles.wait_workflow import LocalWaitWorkflow


async def wait_row(database, query, values, expected, timeout=30):
    def read():
        with psycopg.connect(database) as connection:
            row = connection.execute(query,values).fetchone()
            return row[0] if row else None
    async with asyncio.timeout(timeout):
        while await asyncio.to_thread(read) != expected:
            await asyncio.sleep(.1)


@pytest.mark.asyncio
async def test_pending_timer_survives_worker_kill_database_restore_and_server_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE","fixture")
    admission,outbox,admitted = scenario(2)
    database = admission.database_url
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:case_operator','allow','{}',now()+interval '1 hour')", (admission.workspace_id,str(admission.subject_id)))
    service = CycleGovernance(database,workspace_id=admission.workspace_id,subject_id=admission.subject_id)
    opened = service.open_case(admitted["cycle_id"],target="cycle",kind="manual_review",failure_class="manual_review",owner_id=service.subject_id,
        deadline=datetime.now(timezone.utc)+timedelta(seconds=8),reason="snapshot restore fixture",artifact_sha256="d"*64,account_ref="fixture-account")
    waits = RuntimeWaits(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    job = waits.pending()[0]
    queue = "salience-v4-local-"+uuid4().hex
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    driver = FixtureWaitDriver(client,waits,task_queue=queue)
    # Start committed handoff, simulate lost start acknowledgment, then retry.
    original_started = waits.started
    monkeypatch.setattr(waits,"started",lambda *args: (_ for _ in ()).throw(OSError("fixture lost ack")))
    with pytest.raises(OSError):
        await driver.dispatch_one()
    monkeypatch.setattr(waits,"started",original_started)
    assert await driver.dispatch_one()
    runtime_id = "salience-v4-wait:"+str(job["id"])
    first_run = (await client.get_workflow_handle(runtime_id).describe()).run_id
    restored_name = "v4_wait_restore_"+uuid4().hex
    restored_database = urlunsplit(urlsplit(database)._replace(path="/"+restored_name))
    environment = os.environ | {"V4_FIXTURE_AUTOWAITS":"1", "V4_FIXTURE_WAIT_OPERATOR":str(service.subject_id),
        "V4_FIXTURE_WORKSPACE":str(service.workspace_id),"V4_FIXTURE_QUEUE":queue,
        "PYTHONPATH":str(Path(__file__).resolve().parents[2]/"src")}
    logs = tmp_path/"wait-worker.log"
    def worker(database_url):
        with logs.open("a") as output:
            return subprocess.Popen([sys.executable,"-m","salience.cycles.runtime"],env=environment | {"TEST_DATABASE_URL":database_url},stdout=output,stderr=subprocess.STDOUT)
    first = worker(database)
    second = None
    try:
        async with asyncio.timeout(20):
            while not any(event.HasField("timer_started_event_attributes") for event in (await client.get_workflow_handle(runtime_id).fetch_history()).events):
                await asyncio.sleep(.1)
        first.kill()
        await asyncio.to_thread(first.wait,10)
        container = subprocess.run(["docker","compose","ps","-q","postgres"],capture_output=True,text=True,check=True).stdout.strip()
        snapshot = tmp_path/"fixture.dump"
        with snapshot.open("wb") as output:
            subprocess.run(["docker","exec",container,"pg_dump","-U","salience","-d","salience","-Fc"],stdout=output,check=True)
        with psycopg.connect(database,autocommit=True) as connection:
            connection.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(restored_name)))
        with snapshot.open("rb") as source:
            subprocess.run(["docker","exec","-i",container,"pg_restore","-U","salience","-d",restored_name,"--exit-on-error"],stdin=source,check=True,capture_output=True)
        # Persisted Temporal server state is restored by restarting its process;
        # the backup/restore drill here covers PostgreSQL, not production R66.
        await asyncio.to_thread(subprocess.run,["docker","compose","restart","temporal"],check=True,capture_output=True)
        await asyncio.sleep(1)
        second = worker(restored_database)
        await wait_row(restored_database,"SELECT state FROM v4_recovery_cases WHERE id=%s",(opened["case_id"],),"suspended",timeout=45)
        await wait_row(restored_database,"SELECT count(*) FROM v4_notification_acceptances a JOIN v4_case_notifications n ON n.id=a.notification_id WHERE n.case_id=%s AND n.kind='escalation'",(opened["case_id"],),1)
        result = await asyncio.wait_for(client.get_workflow_handle(runtime_id).result(),15)
        assert result["state"] == "suspended"
        assert (await client.get_workflow_handle(runtime_id).describe()).run_id == first_run
        await Replayer(workflows=[LocalWaitWorkflow]).replay_workflow(await client.get_workflow_handle(runtime_id).fetch_history())
        restored = RuntimeWaits(restored_database,workspace_id=service.workspace_id,subject_id=service.subject_id)
        assert restored.fire(job["id"]) == result
        with psycopg.connect(restored_database) as connection:
            assert connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s",(admitted["cycle_id"],)).fetchone() == connection.execute("SELECT context_id,operation_id FROM v4_recovery_cases WHERE id=%s",(opened["case_id"],)).fetchone()
            assert connection.execute("SELECT count(*) FROM v4_case_events WHERE case_id=%s AND action='escalated'",(opened["case_id"],)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM v4_review_decisions d JOIN v4_case_reviews r ON r.id=d.review_id WHERE r.case_id=%s",(opened["case_id"],)).fetchone()[0] == 0
    finally:
        for process in (first,second):
            if process is not None and process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait,10)
        with psycopg.connect(database,autocommit=True) as connection:
            connection.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {} WITH(FORCE)").format(psycopg.sql.Identifier(restored_name)))
