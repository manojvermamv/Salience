"""Selected-opportunity legacy starts retain canonical authority and identity."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import psycopg
import pytest
from test_v4_cycle_admission import cycles
from test_v4_cadence_policy import policy
from test_v4_legacy_dispatch import legacy


def selected_command(legacy):
    from salience.cycles.legacy_dispatch import LegacyBriefCommand
    bridge, command, service, database = legacy
    with psycopg.connect(database) as connection:
        program = connection.execute("SELECT payload->>'content_program_id' FROM v4_goal_revisions WHERE goal_id=%s AND revision=1", (command.goal_id,)).fetchone()[0]
        opportunity = connection.execute("INSERT INTO topic_opportunities(workspace_id,content_program_id,fingerprint,topic,score,explanation) VALUES(%s,%s,%s,'Selected fixture opportunity',0.8,'Fixture selection') RETURNING id", (service.workspace_id, program, uuid4().hex)).fetchone()[0]
    return LegacyBriefCommand(**(command.model_dump() | {"contract_version": "LegacyBriefDispatch.local.v1", "selected_opportunity_id": opportunity}))


@pytest.mark.asyncio
async def test_selected_brief_uses_original_permit_job_and_workflow(legacy):
    from temporalio.client import Client
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    from salience.cycles.legacy_runtime import build_legacy_worker
    bridge, _, service, database = legacy
    command = selected_command(legacy)
    submitted = bridge.submit_brief(command)
    assert bridge.submit_brief(command) == submitted
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box = CycleOutbox(database, workspace_id=service.workspace_id, delivery_lane="legacy")
    transport = TemporalCycleTransport(client, task_queue=bridge.task_queue)
    async with build_legacy_worker(client, task_queue=bridge.task_queue, outbox=box):
        assert await box.dispatch_one(transport)
        execution = client.get_workflow_handle("salience-v4-legacy-operation:"+submitted["operation_id"])
        async with asyncio.timeout(10):
            while True:
                try:
                    await execution.describe()
                    break
                except Exception:
                    await asyncio.sleep(0.05)
        result = await asyncio.wait_for(execution.result(), 20)
        assert result["job_id"] == submitted["job_id"] and result["state"] == "completed"
        with psycopg.connect(database) as connection:
            assert str(connection.execute("SELECT topic_opportunity_id FROM content_brief_versions WHERE id=%s", (result["content_brief_id"],)).fetchone()[0]) == str(command.selected_opportunity_id)
            assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s", (submitted["cycle_id"],)).fetchone()[0] == 1
        service.close(submitted["cycle_id"], disposition="completed", reason="Selected brief verified")
        assert await box.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(submitted["cycle_id"])).result(), 10)


def test_signed_selected_brief_sdk_cli_and_disabled_legacy_writes(legacy, monkeypatch, capsys):
    import json
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient
    from salience.api.p0 import create_p0_app
    from salience.sdk.client import SalienceClient
    from salience.cli import main
    bridge, _, service, database = legacy
    command = selected_command(legacy)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app = create_p0_app(database_url=database, workspace_id=service.workspace_id, issuer="https://fixture.invalid", audience="fixture", public_key=key.public_key(), enable_legacy_dispatch=True, legacy_fixture_queue=bridge.task_queue)
    now = datetime.now(timezone.utc)
    token = jwt.encode({"iss": "https://fixture.invalid", "aud": "fixture", "sub": str(service.subject_id), "iat": now, "nbf": now, "exp": now+timedelta(minutes=5)}, key, algorithm="RS256")
    async def direct_write(*args, **kwargs):
        raise AssertionError("Direct legacy Temporal write must remain unreachable")
    for name in ("start_dummy", "start_intelligence", "start_content_brief", "start_creative", "start_publication", "cancel_publication"):
        monkeypatch.setattr(app.state.control_plane, name, direct_write)
    with TestClient(app) as client:
        monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: client.request(method, url, **{k:v for k,v in kwargs.items() if k!="timeout"}))
        sdk = SalienceClient("http://testserver", token).legacy_intelligence
        first = sdk.submit_brief(command.model_dump(mode="json"))
        assert first["state"] == "queued"
        monkeypatch.setenv("SALIENCE_CONTROL_JWT", token)
        monkeypatch.setenv("SALIENCE_CONTROL_URL", "http://testserver")
        main(["legacy-intelligence", "brief", "--command-json", json.dumps(command.model_dump(mode="json"))])
        assert json.loads(capsys.readouterr().out) == first
        headers = {"Authorization": "Bearer "+token, "X-Salience-Scopes": "control:write,admin:*"}
        assert client.post(f"/v1/intelligence/opportunities/{command.selected_opportunity_id}/briefs", headers=headers, json=command.model_dump(mode="json") | {"selected_opportunity_id": str(uuid4())}).status_code == 409
        assert client.post(f"/v1/intelligence/opportunities/{uuid4()}/briefs", headers=headers, json=command.model_dump(mode="json")).status_code == 403
        for path in ("/v1/jobs/dummy", "/v1/intelligence/schedules", "/v1/creative/runs", "/v1/publication/runs", f"/v1/publication/runs/{uuid4()}/cancel", "/v1/publication/schedules"):
            assert client.post(path, headers=headers, json={}).status_code == 403, path
    with pytest.raises(PermissionError, match="outside original"):
        bridge.submit_brief(command.model_copy(update={"selected_opportunity_id": uuid4(), "request": command.request.model_copy(update={"idempotency_key": "foreign"})}))
    # Current native reads must reject a reference whose workspace was changed
    # after admission; a frozen selected ID cannot grant cross-workspace reads.
    from salience.intelligence.repository import IntelligenceRepository
    with psycopg.connect(database) as connection:
        foreign = uuid4()
        connection.execute("INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'Foreign fixture')", (foreign, str(foreign)))
        program = connection.execute("SELECT content_program_id FROM topic_opportunities WHERE id=%s", (command.selected_opportunity_id,)).fetchone()[0]
        connection.execute("UPDATE topic_opportunities SET workspace_id=%s WHERE id=%s", (foreign, command.selected_opportunity_id))
    with pytest.raises(KeyError):
        asyncio.run(IntelligenceRepository(database).opportunity_details(opportunity_id=str(command.selected_opportunity_id), program_id=str(program)))
