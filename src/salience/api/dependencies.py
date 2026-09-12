from dataclasses import dataclass, field
from secrets import token_hex
from typing import Protocol
from uuid import uuid4

from fastapi import HTTPException, Request, status
from temporalio.client import Client

from salience.workflows.jobs import DummyWorkflowRequest, DurableDummyWorkflow
from salience.workflows.persistence import CanonicalJobStore


@dataclass(frozen=True)
class RequestContext:
    scopes: frozenset[str]


@dataclass(frozen=True)
class ControlJob:
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    audit_events: list[dict[str, object]]
    provenance_records: list[dict[str, object]]
    cost_entries: list[dict[str, object]]


@dataclass(frozen=True)
class ControlWorkspace:
    workspace_id: str
    slug: str
    display_name: str


@dataclass(frozen=True)
class ControlContentProgram:
    content_program_id: str
    workspace_id: str
    slug: str
    name: str
    niche: str


class ControlPlane(Protocol):
    async def start_dummy(self, *, dry_run: bool, idempotency_key: str) -> ControlJob: ...

    async def get_job(self, job_id: str) -> ControlJob | None: ...

    async def create_workspace(self, *, slug: str, display_name: str) -> ControlWorkspace: ...

    async def create_content_program(
        self, *, workspace_id: str, slug: str, name: str, niche: str
    ) -> ControlContentProgram: ...


@dataclass
class InMemoryControlPlane:
    """Deterministic API harness; production implementations own persistence."""

    _jobs: dict[str, ControlJob] = field(default_factory=dict)
    _workspaces: dict[str, ControlWorkspace] = field(default_factory=dict)
    _content_programs: dict[tuple[str, str], ControlContentProgram] = field(
        default_factory=dict
    )

    async def start_dummy(self, *, dry_run: bool, idempotency_key: str) -> ControlJob:
        job_id = str(uuid4())
        trace_id = token_hex(16)
        job = ControlJob(
            job_id=job_id,
            state="succeeded" if dry_run else "queued",
            dry_run=dry_run,
            trace_id=trace_id,
            audit_events=[
                {
                    "action": "job.started",
                    "outcome": "allowed",
                    "idempotency_key": idempotency_key,
                }
            ],
            provenance_records=[
                {"origin_type": "control_plane", "trace_id": trace_id}
            ],
            cost_entries=[
                {"kind": "estimated", "micros": 0},
                {"kind": "actual", "micros": 0},
            ],
        )
        self._jobs[job_id] = job
        return job

    async def get_job(self, job_id: str) -> ControlJob | None:
        return self._jobs.get(job_id)

    async def create_workspace(self, *, slug: str, display_name: str) -> ControlWorkspace:
        existing = next(
            (workspace for workspace in self._workspaces.values() if workspace.slug == slug),
            None,
        )
        workspace = ControlWorkspace(
            workspace_id=existing.workspace_id if existing is not None else str(uuid4()),
            slug=slug,
            display_name=display_name,
        )
        self._workspaces[workspace.workspace_id] = workspace
        return workspace

    async def create_content_program(
        self, *, workspace_id: str, slug: str, name: str, niche: str
    ) -> ControlContentProgram:
        if workspace_id not in self._workspaces:
            raise KeyError(workspace_id)
        program = ControlContentProgram(
            content_program_id=str(uuid4()),
            workspace_id=workspace_id,
            slug=slug,
            name=name,
            niche=niche,
        )
        self._content_programs[(workspace_id, slug)] = program
        return program


class TemporalControlPlane:
    """Production control-plane adapter backed by canonical data and Temporal."""

    def __init__(
        self, *, database_url: str, temporal_target: str, task_queue: str
    ) -> None:
        self._store = CanonicalJobStore(database_url)
        self._temporal_target = temporal_target
        self._task_queue = task_queue

    async def start_dummy(self, *, dry_run: bool, idempotency_key: str) -> ControlJob:
        if not dry_run:
            raise PermissionError("non-dry-run jobs require an explicit policy adapter")
        existing = await self._store.job_by_idempotency_key(idempotency_key)
        if existing is not None:
            return ControlJob(
                job_id=existing.job_id,
                state=existing.state,
                dry_run=existing.dry_run,
                trace_id=existing.trace_id,
                audit_events=existing.audit_events,
                provenance_records=existing.provenance_records,
                cost_entries=existing.cost_entries,
            )
        workflow_id = f"control-dummy-{uuid4()}"
        run = await self._store.create_run(
            workflow_run_id=workflow_id,
            task_queue=self._task_queue,
            idempotency_key=idempotency_key,
            dry_run=True,
        )
        client = await Client.connect(self._temporal_target)
        await client.start_workflow(
            DurableDummyWorkflow.run,
            DummyWorkflowRequest(idempotency_key=idempotency_key, dry_run=True),
            id=workflow_id,
            task_queue=self._task_queue,
        )
        job = await self.get_job(str(run.job_id))
        if job is None:
            raise RuntimeError("started canonical job is not inspectable")
        return job

    async def get_job(self, job_id: str) -> ControlJob | None:
        snapshot = await self._store.job_snapshot(job_id)
        if snapshot is None:
            return None
        return ControlJob(
            job_id=snapshot.job_id,
            state=snapshot.state,
            dry_run=snapshot.dry_run,
            trace_id=snapshot.trace_id,
            audit_events=snapshot.audit_events,
            provenance_records=snapshot.provenance_records,
            cost_entries=snapshot.cost_entries,
        )

    async def create_workspace(self, *, slug: str, display_name: str) -> ControlWorkspace:
        workspace = await self._store.create_workspace(
            slug=slug, display_name=display_name
        )
        return ControlWorkspace(
            workspace_id=workspace.workspace_id,
            slug=workspace.slug,
            display_name=workspace.display_name,
        )

    async def create_content_program(
        self, *, workspace_id: str, slug: str, name: str, niche: str
    ) -> ControlContentProgram:
        program = await self._store.create_content_program(
            workspace_id=workspace_id, slug=slug, name=name, niche=niche
        )
        return ControlContentProgram(
            content_program_id=program.content_program_id,
            workspace_id=program.workspace_id,
            slug=program.slug,
            name=program.name,
            niche=program.niche,
        )


def require_scope(required_scope: str):
    async def dependency(request: Request) -> RequestContext:
        authorization = request.headers.get("Authorization", "")
        expected = request.app.state.control_token
        if authorization != f"Bearer {expected}":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        scopes = frozenset(
            scope.strip()
            for scope in request.headers.get("X-Salience-Scopes", "").split(",")
            if scope.strip()
        )
        if required_scope not in scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return RequestContext(scopes=scopes)

    return dependency
