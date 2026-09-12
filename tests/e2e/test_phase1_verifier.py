import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from temporalio.client import Client

from salience.api.app import create_app
from salience.api.dependencies import TemporalControlPlane
from salience.fixtures.mock_effect_memory import MockEffectProvider
from salience.workflows.jobs import WorkflowScenarioState, build_worker
from salience.workflows.persistence import CanonicalJobStore


@pytest.mark.asyncio
async def test_phase_one_control_plane_starts_and_inspects_a_durable_dry_run() -> None:
    temporal_target = os.environ["TEST_TEMPORAL_TARGET"]
    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(temporal_target)
    store = CanonicalJobStore(database_url)
    worker = build_worker(
        temporal_client,
        task_queue="salience-phase-one",
        state=WorkflowScenarioState(provider=MockEffectProvider(), store=store),
    )
    worker_task = asyncio.create_task(worker.run())
    app = create_app(
        control_token="phase-one-token",
        control_plane=TemporalControlPlane(
            database_url=database_url,
            temporal_target=temporal_target,
            task_queue="salience-phase-one",
        ),
    )
    headers = {
        "Authorization": "Bearer phase-one-token",
        "X-Salience-Scopes": "control:read,control:write",
    }
    try:
        with TestClient(app) as client:
            idempotency_key = f"phase-one-{uuid4()}"
            started = client.post(
                "/v1/jobs/dummy",
                headers=headers,
                json={"dry_run": True, "idempotency_key": idempotency_key},
            )
            assert started.status_code == 202
            job_id = started.json()["job_id"]
            repeated = client.post(
                "/v1/jobs/dummy",
                headers=headers,
                json={"dry_run": True, "idempotency_key": idempotency_key},
            )
            assert repeated.json()["job_id"] == job_id
            for _ in range(30):
                await asyncio.sleep(0.1)
                status = client.get(f"/v1/jobs/{job_id}", headers=headers)
                if status.json()["state"] == "succeeded":
                    break
            assert status.json()["state"] == "succeeded"
            inspection = client.get(f"/v1/jobs/{job_id}/inspection", headers=headers)
            assert inspection.status_code == 200
            assert inspection.json()["audit_events"]
            assert inspection.json()["provenance_records"]
            assert inspection.json()["trace_id"]
    finally:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)
