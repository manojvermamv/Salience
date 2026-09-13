"""Deployable Temporal worker and a Compose-level crash/recovery verifier."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from temporalio.client import Client

from salience.config import Settings
from salience.fixtures.mock_effect_provider import HttpMockEffectProvider
from salience.workflows.jobs import (
    TASK_QUEUE_PREFIX,
    DummyWorkflowRequest,
    DurableDummyWorkflow,
    DummyActivities,
    RestartScenarioResult,
    WorkflowScenarioState,
    build_worker,
)
from salience.workflows.intelligence import (
    IntelligenceActivities,
    IntelligenceLoopWorkflow,
    IntelligenceWorkflowState,
)
from salience.workflows.creative import (
    CreativeActivities,
    CreativeProductionWorkflow,
    CreativeWorkflowState,
)
from salience.agents.fixtures import fixture_agent_service
from salience.creative.repository import CreativeRepository
from salience.governance.cost_repository import CostReservationRepository
from salience.intelligence.repository import IntelligenceRepository
from salience.research.rss import ConfiguredRssResearchConnector
from salience.workflows.persistence import CanonicalJobStore
from temporalio.worker import Worker


@dataclass(frozen=True)
class ComposeRestartResult:
    status: str
    provider_effect_calls: int
    reconciled: bool
    canonical_checkpoint_count: int
    canonical_effect_count: int


async def run_worker() -> None:
    settings = Settings.from_environment()
    client = await Client.connect(settings.temporal_target)
    state = WorkflowScenarioState(
        provider=HttpMockEffectProvider(settings.mock_effect_provider_url),
        store=CanonicalJobStore(settings.database_url),
    )
    intelligence_state = IntelligenceWorkflowState(
        store=CanonicalJobStore(settings.database_url),
        repository=IntelligenceRepository(settings.database_url),
        agents=_intelligence_agent_service(settings),
    )
    creative_state = CreativeWorkflowState(
        store=CanonicalJobStore(settings.database_url),
        intelligence_repository=IntelligenceRepository(settings.database_url),
        creative_repository=CreativeRepository(settings.database_url),
        agents=fixture_agent_service(),
        cost_repository=CostReservationRepository(settings.database_url),
    )
    worker = build_deployable_worker(
        client,
        task_queue=settings.worker_task_queue,
        dummy_state=state,
        intelligence_state=intelligence_state,
        creative_state=creative_state,
    )
    await worker.run()


def build_deployable_worker(
    client: Client,
    *,
    task_queue: str,
    dummy_state: WorkflowScenarioState,
    intelligence_state: IntelligenceWorkflowState,
    creative_state: CreativeWorkflowState | None = None,
) -> Worker:
    """Register every owned workflow on one task queue without split ownership."""

    dummy = DummyActivities(dummy_state)
    intelligence = IntelligenceActivities(intelligence_state)
    creative = CreativeActivities(creative_state) if creative_state is not None else None
    workflows = [DurableDummyWorkflow, IntelligenceLoopWorkflow]
    activities = [
        dummy.checkpoint,
        dummy.external_effect,
        dummy.terminal,
        dummy.dead_letter,
        intelligence.fetch,
        intelligence.normalize,
        intelligence.rank,
        intelligence.strategy,
        intelligence.queue,
        intelligence.complete,
        intelligence.cancel,
        intelligence.packages,
        intelligence.claims,
        intelligence.brief,
    ]
    if creative is not None:
        workflows.append(CreativeProductionWorkflow)
        activities.extend(
            [
                creative.load_brief,
                creative.script,
                creative.verify_script,
                creative.direction,
                creative.authorize,
                creative.submit_or_reconcile,
                creative.await_provider,
                creative.import_validate,
                creative.distribute,
                creative.final_gate,
                creative.complete,
                creative.denied,
                creative.cancel,
            ]
        )
    return Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=activities,
    )


def _intelligence_agent_service(settings: Settings):
    if not settings.research_rss_feed_urls:
        return fixture_agent_service()
    return fixture_agent_service(
        research_connector=ConfiguredRssResearchConnector(
            feed_urls=settings.research_rss_feed_urls,
            allowed_domains=frozenset(settings.research_allowed_domains),
            timeout_seconds=settings.research_request_timeout_seconds,
            max_response_bytes=settings.research_max_response_bytes,
        ),
        research_source_label="configured-rss",
    )


def main() -> None:
    asyncio.run(run_worker())


def _compose(
    *, project_directory: Path, project_name: str, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {"CONTROL_PLANE_TOKEN": "compose-verifier-token"}
    return subprocess.run(
        ["docker", "compose", "--project-name", project_name, *arguments],
        cwd=project_directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def _service_ip(*, project_directory: Path, project_name: str, service: str) -> str:
    container_id = _compose(
        project_directory=project_directory,
        project_name=project_name,
        arguments=["ps", "-q", service],
    ).stdout.strip()
    if not container_id:
        raise RuntimeError(f"Compose service {service} has no container")
    inspection = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            container_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return inspection.stdout.strip()


async def _wait_for_worker_exit(
    *, project_directory: Path, project_name: str, timeout_seconds: float = 20
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = _compose(
            project_directory=project_directory,
            project_name=project_name,
            arguments=["ps", "--all", "--format", "{{.State}}", "worker"],
        ).stdout.strip()
        if state == "exited":
            return
        await asyncio.sleep(0.25)
    raise TimeoutError("worker did not exit after the mock provider accepted the effect")


async def run_compose_restart_scenario(
    *, project_directory: Path
) -> ComposeRestartResult:
    """Hard-kill a real worker after remote acceptance and verify reconciliation."""

    project_name = f"salience-recovery-{uuid4().hex[:12]}"
    try:
        await asyncio.to_thread(
            _compose,
            project_directory=project_directory,
            project_name=project_name,
            arguments=["up", "-d", "--build", "postgres", "temporal", "mock-effect-provider"],
        )
        await asyncio.to_thread(
            _compose,
            project_directory=project_directory,
            project_name=project_name,
            arguments=["run", "--rm", "migrate"],
        )
        await asyncio.to_thread(
            _compose,
            project_directory=project_directory,
            project_name=project_name,
            arguments=["up", "-d", "worker"],
        )

        temporal_ip = await asyncio.to_thread(
            _service_ip,
            project_directory=project_directory,
            project_name=project_name,
            service="temporal",
        )
        postgres_ip = await asyncio.to_thread(
            _service_ip,
            project_directory=project_directory,
            project_name=project_name,
            service="postgres",
        )
        provider_ip = await asyncio.to_thread(
            _service_ip,
            project_directory=project_directory,
            project_name=project_name,
            service="mock-effect-provider",
        )
        client = await Client.connect(f"{temporal_ip}:7233")
        store = CanonicalJobStore(
            f"postgresql://salience:salience@{postgres_ip}:5432/salience"
        )
        workflow_id = f"compose-restart-{uuid4()}"
        idempotency_key = f"compose-effect-{uuid4()}"
        run = await store.create_run(
            workflow_run_id=workflow_id,
            task_queue="salience-phase-one",
            idempotency_key=idempotency_key,
        )
        handle = await client.start_workflow(
            DurableDummyWorkflow.run,
            DummyWorkflowRequest(
                idempotency_key=idempotency_key,
                crash_after_remote_acceptance=True,
            ),
            id=workflow_id,
            task_queue="salience-phase-one",
        )
        await _wait_for_worker_exit(
            project_directory=project_directory, project_name=project_name
        )
        await asyncio.to_thread(
            _compose,
            project_directory=project_directory,
            project_name=project_name,
            arguments=["start", "worker"],
        )
        status = await asyncio.wait_for(handle.result(), timeout=30)
        counts = await store.counts(run)
        async with httpx.AsyncClient(timeout=5) as provider_client:
            provider_response = await provider_client.get(
                f"http://{provider_ip}:8081/health/live"
            )
        provider_response.raise_for_status()
        provider_effect_calls = provider_response.json()["accepted_effect_count"]
        return ComposeRestartResult(
            status=status,
            provider_effect_calls=provider_effect_calls,
            reconciled=await store.effect_was_reconciled(run, idempotency_key),
            canonical_checkpoint_count=counts.checkpoint_count,
            canonical_effect_count=counts.effect_count,
        )
    except Exception as error:
        logs = await asyncio.to_thread(
            _compose,
            project_directory=project_directory,
            project_name=project_name,
            arguments=["logs", "--no-color", "worker"],
        )
        raise RuntimeError(f"Compose worker recovery failed:\n{logs.stdout}") from error
    finally:
        await asyncio.to_thread(
            _compose,
            project_directory=project_directory,
            project_name=project_name,
            arguments=["down", "--volumes", "--remove-orphans"],
        )


if __name__ == "__main__":
    main()
