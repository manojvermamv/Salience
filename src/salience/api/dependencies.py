from dataclasses import dataclass, field
from datetime import timedelta
from secrets import token_hex
from typing import Protocol
from uuid import uuid4

from fastapi import HTTPException, Request, status
from temporalio.client import Client

from salience.workflows.jobs import DummyWorkflowRequest, DurableDummyWorkflow
from salience.workflows.intelligence import (
    INTELLIGENCE_WORKFLOW_TYPE,
    IntelligenceLoopRequest,
)
from salience.workflows.persistence import CanonicalJobStore
from salience.workflows.schedules import ScheduleRequest, TemporalScheduleService
from salience.intelligence.repository import IntelligenceRepository


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
    output: dict[str, object] = field(default_factory=dict)


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


@dataclass(frozen=True)
class ControlIntelligenceRun:
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    output: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ControlSchedule:
    schedule_id: str
    workspace_id: str
    content_program_id: str
    name: str
    job_type: str
    schedule_expression: str


@dataclass(frozen=True)
class ControlContentBrief:
    brief_id: str
    content_program_id: str
    opportunity_id: str
    package_id: str
    content: dict[str, object]
    claim_ids: list[str]


class ControlPlane(Protocol):
    async def start_dummy(self, *, dry_run: bool, idempotency_key: str) -> ControlJob: ...

    async def get_job(self, job_id: str) -> ControlJob | None: ...

    async def create_workspace(self, *, slug: str, display_name: str) -> ControlWorkspace: ...

    async def create_content_program(
        self, *, workspace_id: str, slug: str, name: str, niche: str
    ) -> ControlContentProgram: ...

    async def start_intelligence(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        niche: str,
        dry_run: bool,
        idempotency_key: str,
    ) -> ControlIntelligenceRun: ...

    async def get_intelligence(self, job_id: str) -> ControlIntelligenceRun | None: ...

    async def create_intelligence_schedule(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        name: str,
        every_seconds: int,
        niche: str,
    ) -> ControlSchedule: ...

    async def start_content_brief(
        self,
        *,
        opportunity_id: str,
        content_program_id: str,
        idempotency_key: str,
        dry_run: bool,
    ) -> ControlIntelligenceRun: ...

    async def get_content_brief(self, brief_id: str) -> ControlContentBrief | None: ...

    async def get_content_brief_lineage(self, brief_id: str) -> dict[str, object] | None: ...


@dataclass
class InMemoryControlPlane:
    """Deterministic API harness; production implementations own persistence."""

    _jobs: dict[str, ControlJob] = field(default_factory=dict)
    _workspaces: dict[str, ControlWorkspace] = field(default_factory=dict)
    _content_programs: dict[tuple[str, str], ControlContentProgram] = field(
        default_factory=dict
    )
    _intelligence_runs: dict[str, ControlIntelligenceRun] = field(default_factory=dict)
    _schedules: dict[tuple[str, str], ControlSchedule] = field(default_factory=dict)
    _briefs: dict[str, ControlContentBrief] = field(default_factory=dict)

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

    async def start_intelligence(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        niche: str,
        dry_run: bool,
        idempotency_key: str,
        selected_opportunity_id: str | None = None,
    ) -> ControlIntelligenceRun:
        if not any(
            program.content_program_id == content_program_id and program.workspace_id == workspace_id
            for program in self._content_programs.values()
        ):
            raise KeyError(content_program_id)
        existing = next(
            (
                run
                for run in self._intelligence_runs.values()
                if run.output.get("idempotency_key") == idempotency_key
            ),
            None,
        )
        if existing is not None:
            return existing
        run = ControlIntelligenceRun(
            job_id=str(uuid4()),
            state="succeeded",
            dry_run=dry_run,
            trace_id=token_hex(16),
            output={
                "contract_version": "IntelligenceRunResult@v1",
                "niche": niche,
                "idempotency_key": idempotency_key,
                "selected_opportunity_id": selected_opportunity_id,
            },
        )
        self._intelligence_runs[run.job_id] = run
        return run

    async def start_content_brief(
        self,
        *,
        opportunity_id: str,
        content_program_id: str,
        idempotency_key: str,
        dry_run: bool,
    ) -> ControlIntelligenceRun:
        program = next(
            (
                candidate
                for candidate in self._content_programs.values()
                if candidate.content_program_id == content_program_id
            ),
            None,
        )
        if program is None:
            raise KeyError(content_program_id)
        return await self.start_intelligence(
            workspace_id=program.workspace_id,
            content_program_id=content_program_id,
            niche=program.niche,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            selected_opportunity_id=opportunity_id,
        )

    async def get_content_brief(self, brief_id: str) -> ControlContentBrief | None:
        return self._briefs.get(brief_id)

    async def get_content_brief_lineage(self, brief_id: str) -> dict[str, object] | None:
        brief = await self.get_content_brief(brief_id)
        if brief is None:
            return None
        return {
            "opportunity_id": brief.opportunity_id,
            "package_id": brief.package_id,
            "claim_ids": brief.claim_ids,
            "source_ids": [],
            "fetch_ids": [],
            "signal_ids": [],
        }

    async def get_intelligence(self, job_id: str) -> ControlIntelligenceRun | None:
        return self._intelligence_runs.get(job_id)

    async def create_intelligence_schedule(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        name: str,
        every_seconds: int,
        niche: str,
    ) -> ControlSchedule:
        if every_seconds <= 0:
            raise ValueError("every_seconds must be positive")
        if not any(
            program.content_program_id == content_program_id and program.workspace_id == workspace_id
            for program in self._content_programs.values()
        ):
            raise KeyError(content_program_id)
        schedule = ControlSchedule(
            schedule_id=f"{workspace_id}:{name}",
            workspace_id=workspace_id,
            content_program_id=content_program_id,
            name=name,
            job_type="intelligence_research",
            schedule_expression=f"every {every_seconds}s",
        )
        self._schedules[(workspace_id, name)] = schedule
        return schedule

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
        self._intelligence = IntelligenceRepository(database_url)
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

    async def start_intelligence(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        niche: str,
        dry_run: bool,
        idempotency_key: str,
        selected_opportunity_id: str | None = None,
    ) -> ControlIntelligenceRun:
        if not dry_run:
            raise PermissionError("non-dry-run intelligence requires an explicit policy adapter")
        existing = await self._store.job_by_idempotency_key(idempotency_key)
        if existing is not None:
            return _intelligence_from_job(existing)
        workflow_id = f"control-intelligence-{uuid4()}"
        run = await self._store.create_intelligence_run(
            workflow_run_id=workflow_id,
            task_queue=self._task_queue,
            workspace_id=workspace_id,
            content_program_id=content_program_id,
            niche=niche,
            idempotency_key=idempotency_key,
            dry_run=True,
        )
        client = await Client.connect(self._temporal_target)
        await client.start_workflow(
            INTELLIGENCE_WORKFLOW_TYPE,
            IntelligenceLoopRequest(
                workspace_id=workspace_id,
                content_program_id=content_program_id,
                niche=niche,
                idempotency_key=idempotency_key,
                dry_run=True,
                selected_opportunity_id=selected_opportunity_id,
            ),
            id=workflow_id,
            task_queue=self._task_queue,
        )
        return ControlIntelligenceRun(
            job_id=str(run.job_id),
            state="running",
            dry_run=True,
            trace_id=run.trace_context.trace_id,
        )

    async def start_content_brief(
        self,
        *,
        opportunity_id: str,
        content_program_id: str,
        idempotency_key: str,
        dry_run: bool,
    ) -> ControlIntelligenceRun:
        details = await self._intelligence.opportunity_details(
            opportunity_id=opportunity_id, program_id=content_program_id
        )
        return await self.start_intelligence(
            workspace_id=details["workspace_id"],
            content_program_id=content_program_id,
            niche=details["niche"],
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            selected_opportunity_id=opportunity_id,
        )

    async def get_content_brief(self, brief_id: str) -> ControlContentBrief | None:
        details = await self._intelligence.brief_details(brief_id)
        return _content_brief_from_details(details) if details is not None else None

    async def get_content_brief_lineage(self, brief_id: str) -> dict[str, object] | None:
        details = await self._intelligence.brief_details(brief_id)
        if details is None:
            return None
        return await self._intelligence.lineage_for_brief(brief_id)

    async def get_intelligence(self, job_id: str) -> ControlIntelligenceRun | None:
        snapshot = await self._store.job_snapshot(job_id)
        return _intelligence_from_job(snapshot) if snapshot is not None else None

    async def create_intelligence_schedule(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        name: str,
        every_seconds: int,
        niche: str,
    ) -> ControlSchedule:
        if every_seconds <= 0:
            raise ValueError("every_seconds must be positive")
        canonical = await self._store.create_schedule(
            workspace_id=workspace_id,
            content_program_id=content_program_id,
            name=name,
            schedule_expression=f"every {every_seconds}s",
            job_type="intelligence_research",
            payload={"niche": niche, "dry_run": True},
        )
        client = await Client.connect(self._temporal_target)
        await TemporalScheduleService(client).create_every(
            ScheduleRequest(
                schedule_id=canonical.schedule_id,
                task_queue=self._task_queue,
                every=timedelta(seconds=every_seconds),
                workflow_type=INTELLIGENCE_WORKFLOW_TYPE,
                payload={
                    "workspace_id": workspace_id,
                    "content_program_id": content_program_id,
                    "niche": niche,
                    "idempotency_key": f"schedule:{canonical.schedule_id}",
                    "dry_run": True,
                    "contract_version": "IntelligenceRunRequest@v1",
                },
            )
        )
        return ControlSchedule(
            schedule_id=canonical.schedule_id,
            workspace_id=canonical.workspace_id,
            content_program_id=canonical.content_program_id,
            name=canonical.name,
            job_type=canonical.job_type,
            schedule_expression=canonical.schedule_expression,
        )

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
            output=snapshot.output_payload,
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


def _intelligence_from_job(snapshot) -> ControlIntelligenceRun:
    return ControlIntelligenceRun(
        job_id=snapshot.job_id,
        state=snapshot.state,
        dry_run=snapshot.dry_run,
        trace_id=snapshot.trace_id,
        output=snapshot.output_payload,
    )


def _content_brief_from_details(details: dict[str, object]) -> ControlContentBrief:
    return ControlContentBrief(
        brief_id=str(details["brief_id"]),
        content_program_id=str(details["content_program_id"]),
        opportunity_id=str(details["opportunity_id"]),
        package_id=str(details["package_id"]),
        content=dict(details["content"]),
        claim_ids=list(details["claim_ids"]),
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
