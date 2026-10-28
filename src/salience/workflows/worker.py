"""Deployable Temporal worker and a Compose-level crash/recovery verifier."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import inspect
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

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
from salience.workflows.publication import (
    GovernedPublicationWorkflow,
    PublicationActivities,
    PublicationWorkflowState,
)
from salience.publication.repository import PublicationRepository
from salience.agents.fixtures import fixture_agent_service
from salience.creative.repository import CreativeRepository
from salience.governance.cost_repository import CostReservationRepository
from salience.intelligence.repository import IntelligenceRepository
from salience.research.rss import ConfiguredRssResearchConnector
from salience.workflows.persistence import CanonicalJobStore
from temporalio.worker import Worker

from salience.cycles.production_dispatch import (
    ProductionEffectDispatch,
    ProductionPermitRequest,
    PublicationEffectBinding,
    RouteBinding,
)
from salience.cycles.production_routing import (
    PRODUCTION_ACTIVITY_ALLOWLIST,
    PRODUCTION_WORKER_KIND,
    PRODUCTION_WORKFLOW_ALLOWLIST,
    ProductionRouteManifestV1,
    RouteLease,
    RouteLeaseAuthority,
    route_manifest_sha256,
)
from salience.governance.production_policy import ProductionApprovalBinding


@dataclass(frozen=True)
class ComposeRestartResult:
    status: str
    provider_effect_calls: int
    reconciled: bool
    canonical_checkpoint_count: int
    canonical_effect_count: int


@dataclass(frozen=True, slots=True)
class WorkerReadback:
    """Read-only evidence of exact Temporal registration and drained state."""

    route_id: UUID
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str
    temporal_endpoint_id: str
    namespace: str
    task_queue: str
    registered_workflow_types: frozenset[str]
    registered_activity_types: frozenset[str]
    poller_count: int
    active_task_count: int
    observed_at: datetime
    complete: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (self.route_id, self.slot_id, self.owner_id)
        ):
            raise ValueError("worker readback route, slot, and owner UUIDs are required")
        if type(self.fencing_epoch) is not int or self.fencing_epoch < 1:
            raise ValueError("worker readback needs a positive fencing epoch")
        for value in (self.route_manifest_sha256, self.artifact_lock_sha256):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("worker readback digests must be lowercase SHA-256")
        for value in (
            self.temporal_endpoint_id,
            self.namespace,
            self.task_queue,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("worker readback target, namespace, and queue are required")
        for values in (
            self.registered_workflow_types,
            self.registered_activity_types,
        ):
            if not isinstance(values, frozenset) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ValueError("worker readback registrations must be immutable name sets")
        if any(
            type(value) is not int or value < 0
            for value in (self.poller_count, self.active_task_count)
        ):
            raise ValueError("worker readback counts must be non-negative integers")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("worker readback observation time must be timezone-aware")
        if type(self.complete) is not bool:
            raise ValueError("worker readback completeness must be explicit")


class TemporalWorkerReadback(Protocol):
    """Injected read-only view bound to one Temporal target and namespace."""

    def read_worker(self, route_id: UUID, slot_id: UUID) -> WorkerReadback: ...


async def run_worker() -> None:
    if os.environ.get("SALIENCE_DEPLOYMENT_MODE", "fixture") != "fixture":
        raise ValueError("P0 does not authorize effect workers; production workers are not qualified")
    settings = Settings.from_environment()
    from salience.cycles.publication_recovery_compatibility import (
        require_publication_worker_compatibility_from_environment,
    )

    require_publication_worker_compatibility_from_environment(settings.database_url)
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
        provider_timeout_seconds=settings.creative_provider_timeout_seconds,
    )
    publication_state = PublicationWorkflowState(
        store=CanonicalJobStore(settings.database_url),
        repository=PublicationRepository(settings.database_url),
        cost_repository=CostReservationRepository(settings.database_url),
    )
    worker = build_deployable_worker(
        client,
        task_queue=settings.worker_task_queue,
        dummy_state=state,
        intelligence_state=intelligence_state,
        creative_state=creative_state,
        publication_state=publication_state,
    )
    await worker.run()


def build_deployable_worker(
    client: Client,
    *,
    task_queue: str,
    dummy_state: WorkflowScenarioState,
    intelligence_state: IntelligenceWorkflowState,
    creative_state: CreativeWorkflowState | None = None,
    publication_state: PublicationWorkflowState | None = None,
) -> Worker:
    """Register every owned workflow on one task queue without split ownership."""

    dummy = DummyActivities(dummy_state)
    intelligence = IntelligenceActivities(intelligence_state)
    creative = CreativeActivities(creative_state) if creative_state is not None else None
    publication = PublicationActivities(publication_state) if publication_state is not None else None
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
    if publication is not None:
        workflows.append(GovernedPublicationWorkflow)
        activities.extend(
            [
                publication.request,
                publication.authorize,
                publication.delivery,
                publication.submit_or_reconcile,
                publication.await_publication,
                publication.complete,
                publication.denied,
                publication.cancel,
                publication.dead_letter,
            ]
        )
    return Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=activities,
    )


def _validate_routed_lease(
    route: ProductionRouteManifestV1,
    lease: RouteLease,
    route_authority: RouteLeaseAuthority,
    *,
    now: datetime,
) -> None:
    if not isinstance(route, ProductionRouteManifestV1) or not isinstance(lease, RouteLease):
        raise PermissionError("authenticated production route and lease are required")
    if not isinstance(route_authority, RouteLeaseAuthority):
        raise PermissionError("injected route lease authority is required")
    if route_authority.protocol_version != "salience.p1-route-fence.v1":
        raise PermissionError("route lease authority version is unsupported")
    if route.route_tuple.worker_kind != PRODUCTION_WORKER_KIND:
        raise PermissionError("route worker kind is outside the production publication allowlist")
    if set(route.route_tuple.workflow_types) != set(PRODUCTION_WORKFLOW_ALLOWLIST):
        raise PermissionError("route workflow registration is outside the closed allowlist")
    if set(route.route_tuple.activity_types) != set(PRODUCTION_ACTIVITY_ALLOWLIST):
        raise PermissionError("route activity registration is outside the closed allowlist")
    if (
        lease.route_id != route.route_id
        or lease.slot_id not in route.slots
        or lease.route_manifest_sha256 != route_manifest_sha256(route)
        or lease.artifact_lock_sha256 != route.artifact_lock_sha256
        or lease.expires_at <= now
    ):
        raise PermissionError("route lease does not match the authenticated route")
    current = route_authority.read_slot(route.route_id, lease.slot_id)
    if (
        not current.available
        or current.state != "leased"
        or current.lease != lease
        or current.fencing_epoch != lease.fencing_epoch
    ):
        raise PermissionError("route lease is not the current lease")


def _route_binding(route: ProductionRouteManifestV1, lease: RouteLease) -> RouteBinding:
    return RouteBinding(
        deployment_id=route.route_tuple.deployment_id,
        environment_id=route.route_tuple.environment_id,
        route_id=route.route_id,
        slot_id=lease.slot_id,
        owner_id=lease.owner_id,
        fencing_epoch=lease.fencing_epoch,
        route_manifest_sha256=lease.route_manifest_sha256,
        artifact_lock_sha256=lease.artifact_lock_sha256,
        route_tuple_sha256=route.route_tuple_sha256,
    )


def build_routed_worker(
    client: Client,
    *,
    route: ProductionRouteManifestV1,
    lease: RouteLease,
    route_authority: RouteLeaseAuthority,
    database_url: str,
    publication_state: PublicationWorkflowState,
    permit_request_factory: Callable[
        [ProductionApprovalBinding, PublicationEffectBinding], ProductionPermitRequest
    ],
) -> Worker:
    """Assemble one route-bound publication worker after exact lease validation."""
    _validate_routed_lease(
        route,
        lease,
        route_authority,
        now=datetime.now(timezone.utc),
    )
    if getattr(client, "namespace", None) != route.route_tuple.namespace:
        raise PermissionError("Temporal client namespace does not match the authenticated route")
    if (
        hasattr(client, "route_endpoint_id")
        and client.route_endpoint_id != route.route_tuple.temporal_endpoint_id
    ):
        raise PermissionError("Temporal client target does not match the authenticated route")
    if hasattr(client, "route_build_id") and client.route_build_id != route.worker_build_id:
        raise PermissionError("worker build does not match the authenticated route")
    if (
        hasattr(client, "route_artifact_sha256")
        and client.route_artifact_sha256 != route.artifact_sha256
    ):
        raise PermissionError("worker artifact does not match the authenticated route")
    if hasattr(client, "route_image_sha256") and client.route_image_sha256 != route.image_sha256:
        raise PermissionError("worker image does not match the authenticated route")
    if not isinstance(publication_state, PublicationWorkflowState):
        raise ValueError("routed publication workflow state is required")
    if not callable(permit_request_factory):
        raise ValueError("route-bound production permit request factory is required")

    publication_state.production_dispatch = ProductionEffectDispatch(
        database_url,
        route_authority,
    )
    expected_route_binding = _route_binding(route, lease)

    def route_bound_permit_request(
        approval: ProductionApprovalBinding,
        publication: PublicationEffectBinding,
    ) -> ProductionPermitRequest:
        proposed = permit_request_factory(approval, publication)
        if (
            not isinstance(proposed, ProductionPermitRequest)
            or proposed.route != expected_route_binding
            or proposed.publication_binding != publication
            or proposed.operation_id != approval.operation_id
        ):
            raise ValueError("production permit factory returned a mismatched route binding")
        return proposed

    publication_state.production_permit_request_factory = route_bound_permit_request

    publication = PublicationActivities(publication_state)
    workflow_handlers = {
        "GovernedPublicationWorkflow": GovernedPublicationWorkflow,
    }
    activity_handlers = {
        "salience.publication.request": publication.request,
        "salience.publication.authorize": publication.authorize,
        "salience.publication.delivery": publication.delivery,
        "salience.publication.submit_or_reconcile": publication.submit_or_reconcile,
        "salience.publication.await": publication.await_publication,
        "salience.publication.complete": publication.complete,
        "salience.publication.denied": publication.denied,
        "salience.publication.cancel": publication.cancel,
        "salience.publication.dead_letter": publication.dead_letter,
    }
    return Worker(
        client,
        task_queue=route.route_tuple.task_queue,
        workflows=[workflow_handlers[name] for name in route.route_tuple.workflow_types],
        activities=[activity_handlers[name] for name in route.route_tuple.activity_types],
    )


async def connect_routed_worker(
    *,
    route: ProductionRouteManifestV1,
    lease: RouteLease,
    route_authority: RouteLeaseAuthority,
    database_url: str,
    publication_state: PublicationWorkflowState,
    permit_request_factory: Callable[
        [ProductionApprovalBinding, PublicationEffectBinding], ProductionPermitRequest
    ],
    temporal_connect: Callable[..., object],
    compatibility_preflight: Callable[
        [str, ProductionRouteManifestV1, RouteLease], object
    ],
) -> tuple[Client, Worker]:
    """Revalidate authenticated pins and lease before the injected Temporal connector."""
    preflight_result = compatibility_preflight(database_url, route, lease)
    if inspect.isawaitable(preflight_result):
        await preflight_result
    _validate_routed_lease(
        route,
        lease,
        route_authority,
        now=datetime.now(timezone.utc),
    )
    client_result = temporal_connect(
        route.route_tuple.temporal_endpoint_id,
        namespace=route.route_tuple.namespace,
    )
    client = await client_result if inspect.isawaitable(client_result) else client_result
    worker = build_routed_worker(
        client,
        route=route,
        lease=lease,
        route_authority=route_authority,
        database_url=database_url,
        publication_state=publication_state,
        permit_request_factory=permit_request_factory,
    )
    return client, worker


def _require_exact_drained_readback(
    readback: WorkerReadback,
    route: ProductionRouteManifestV1,
    lease: RouteLease,
) -> None:
    expected_workflows = frozenset(route.route_tuple.workflow_types)
    expected_activities = frozenset(route.route_tuple.activity_types)
    if (
        not isinstance(readback, WorkerReadback)
        or not readback.complete
        or readback.route_id != route.route_id
        or readback.slot_id != lease.slot_id
        or readback.owner_id != lease.owner_id
        or readback.fencing_epoch != lease.fencing_epoch
        or readback.route_manifest_sha256 != lease.route_manifest_sha256
        or readback.artifact_lock_sha256 != lease.artifact_lock_sha256
        or readback.temporal_endpoint_id != route.route_tuple.temporal_endpoint_id
        or readback.namespace != route.route_tuple.namespace
        or readback.task_queue != route.route_tuple.task_queue
        or readback.registered_workflow_types != expected_workflows
        or readback.registered_activity_types != expected_activities
        or readback.poller_count != 0
        or readback.active_task_count != 0
    ):
        raise PermissionError(
            "Temporal worker readback is incomplete or does not match the drained route"
        )


async def _read_and_require_drained(
    temporal_readback: TemporalWorkerReadback,
    route: ProductionRouteManifestV1,
    lease: RouteLease,
) -> None:
    result = temporal_readback.read_worker(route.route_id, lease.slot_id)
    readback = await result if inspect.isawaitable(result) else result
    _require_exact_drained_readback(readback, route, lease)


async def run_routed_worker(
    worker: Worker,
    *,
    route: ProductionRouteManifestV1,
    lease: RouteLease,
    route_authority: RouteLeaseAuthority,
    temporal_readback: TemporalWorkerReadback,
    sleep: Callable[[float], object] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> str:
    """Renew a slot at one third of its remaining TTL and drain on lease loss."""
    current = lease
    _validate_routed_lease(route, current, route_authority, now=now())
    run_task = asyncio.create_task(worker.run())
    lease_lost = False
    run_error: BaseException | None = None
    cancellation: asyncio.CancelledError | None = None
    try:
        while not run_task.done():
            remaining = (current.expires_at - now()).total_seconds()
            if remaining <= 0:
                lease_lost = True
                break
            interval = remaining / 3
            pause = sleep(interval)
            if inspect.isawaitable(pause):
                await pause
            if run_task.done():
                break
            if current.expires_at <= now():
                raise PermissionError("route lease expired before renewal")
            renewed = route_authority.renew_slot(current)
            if not isinstance(renewed, RouteLease):
                raise PermissionError("route authority returned an invalid renewal")
            _validate_routed_lease(route, renewed, route_authority, now=now())
            current = renewed
    except asyncio.CancelledError as error:
        cancellation = error
    except Exception:
        lease_lost = True
    if lease_lost or cancellation is not None:
        worker.shutdown()
    try:
        await run_task
    except BaseException as error:
        run_error = error
        worker.shutdown()
    await _read_and_require_drained(temporal_readback, route, current)
    if run_error is not None:
        raise run_error
    if cancellation is not None:
        raise cancellation
    return "lease_lost" if lease_lost else "stopped"


def transfer_routed_worker(
    *,
    route: ProductionRouteManifestV1,
    lease: RouteLease,
    route_authority: RouteLeaseAuthority,
    temporal_readback: TemporalWorkerReadback,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
):
    """Release a routed slot only after exact, complete zero-work readback."""
    _validate_routed_lease(route, lease, route_authority, now=now())
    readback = temporal_readback.read_worker(route.route_id, lease.slot_id)
    if inspect.isawaitable(readback):
        raise TypeError("synchronous route transfer requires synchronous Temporal readback")
    _require_exact_drained_readback(readback, route, lease)
    released = route_authority.release_slot(lease)
    if (
        not released.available
        or released.state != "released"
        or released.route_id != lease.route_id
        or released.slot_id != lease.slot_id
        or released.fencing_epoch != lease.fencing_epoch
    ):
        raise PermissionError("route authority release readback is incomplete")
    return released


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
