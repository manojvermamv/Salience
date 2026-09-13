"""Restart-safe, provider-neutral Phase 7-8 creative production workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from salience.agents.execution import AgentInvocation, AgentService
from salience.creative.contracts import CreativeCapabilityRequest, DistributionPackage
from salience.creative.governance import RightsPolicy
from salience.creative.media import MediaEngine
from salience.creative.repository import CreativeRepository
from salience.creative.scripts import ScriptVerifier
from salience.creative.service import ApprovedProduction, CreativeService
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.persistence import CanonicalJobStore, CanonicalRun

with workflow.unsafe.imports_passed_through():
    from salience.creative.providers import CreativeProvider, FixtureCreativeProvider


CREATIVE_WORKFLOW_TYPE = "CreativeProductionWorkflow"
MAX_CREATIVE_ATTEMPTS = 3


@dataclass(frozen=True)
class CreativeProductionRequest:
    workspace_id: str
    content_program_id: str
    brief_id: str
    idempotency_key: str
    dry_run: bool = True
    target_profile_key: str = "fixture-short-video"
    target_profile_version: int = 1
    contract_version: str = "CreativeProductionRequest@v1"

    def __post_init__(self) -> None:
        if self.contract_version != "CreativeProductionRequest@v1":
            raise ValueError("unsupported creative production request contract")
        if not self.workspace_id or not self.content_program_id or not self.brief_id:
            raise ValueError("workspace, content program, and brief identities are required")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        if self.target_profile_version <= 0:
            raise ValueError("target profile version must be positive")


@dataclass(frozen=True)
class CreativeProductionResult:
    state: str
    job_id: str
    trace_id: str
    ready_package_id: str | None = None
    script_id: str | None = None
    asset_id: str | None = None
    provider_job_id: str | None = None
    provider_submit_count: int = 0
    denial_reason: str | None = None


@dataclass
class CreativeWorkflowState:
    store: CanonicalJobStore
    intelligence_repository: IntelligenceRepository
    creative_repository: CreativeRepository
    agents: AgentService
    provider: CreativeProvider = field(default_factory=FixtureCreativeProvider)
    media: MediaEngine | None = None
    permission_granted: bool = True
    policy_allowed: bool = True
    budget_available_micros: int = 1_000_000
    estimated_cost_micros: int = 100
    crash_at: str | None = None
    crash_reached: asyncio.Event = field(default_factory=asyncio.Event)
    crashed: bool = False


class CreativeActivities:
    def __init__(self, state: CreativeWorkflowState) -> None:
        self._state = state

    async def _run(self) -> CanonicalRun:
        return await self._state.store.run_for_workflow(activity.info().workflow_id)

    async def _checkpoint(self, name: str) -> None:
        await self._state.store.checkpoint(await self._run(), name)

    @activity.defn(name="salience.creative.load_brief")
    async def load_brief(self, request: CreativeProductionRequest) -> dict[str, Any]:
        brief = await self._state.intelligence_repository.exact_content_brief(
            brief_id=request.brief_id,
            workspace_id=request.workspace_id,
            program_id=request.content_program_id,
        )
        await self._checkpoint("creative.brief.loaded")
        return {"request": _request_payload(request), "brief": brief}

    @activity.defn(name="salience.creative.script")
    async def script(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        request = payload["request"]
        brief = payload["brief"]
        agent_input = {
            "brief_id": brief["brief_id"],
            "content_program_id": request["content_program_id"],
            "claim_ids": brief["claim_ids"],
            "evidence_ids": brief["evidence_ids"],
            "target_format": "short_video",
            "target_duration_seconds": 30,
        }
        agent_run = await self._state.agents.invoke_by_id("writer_agent", agent_input)
        agent_run_id = _stage_run_id(run, "writer")
        await self._state.intelligence_repository.record_agent_run(
            run_id=agent_run_id,
            workspace_id=request["workspace_id"],
            program_id=request["content_program_id"],
            job_id=str(run.job_id),
            manifest=self._state.agents.describe_agent("writer_agent"),
            input_payload=agent_input,
            output_payload=agent_run.output,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        script_id = await self._state.creative_repository.record_script(
            workspace_id=request["workspace_id"],
            program_id=request["content_program_id"],
            brief_id=brief["brief_id"],
            script_key=f"creative-{brief['brief_id']}",
            version=1,
            status="draft",
            target_format=agent_run.output["target_format"],
            target_duration_seconds=agent_run.output["target_duration_seconds"],
            script={"sections": agent_run.output["sections"]},
            claim_ids=agent_run.output["claim_ids"],
            evidence_ids=agent_run.output["evidence_ids"],
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        await self._checkpoint("creative.script.persisted")
        return {**payload, "script": agent_run.output, "script_id": script_id, "writer_agent_run_id": agent_run_id}

    @activity.defn(name="salience.creative.verify_script")
    async def verify_script(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = ScriptVerifier().evaluate(payload["script"], payload["brief"])
        if not result.allowed:
            raise ValueError(f"creative script rejected: {','.join(result.blocker_codes)}")
        request = payload["request"]
        approved_script_id = await self._state.creative_repository.record_script(
            workspace_id=request["workspace_id"],
            program_id=request["content_program_id"],
            brief_id=payload["brief"]["brief_id"],
            parent_script_id=payload["script_id"],
            script_key=f"creative-{payload['brief']['brief_id']}",
            version=2,
            status="approved",
            target_format=payload["script"]["target_format"],
            target_duration_seconds=payload["script"]["target_duration_seconds"],
            script={"sections": payload["script"]["sections"]},
            claim_ids=payload["script"]["claim_ids"],
            evidence_ids=payload["script"]["evidence_ids"],
            trace_id=(await self._run()).trace_context.trace_id,
        )
        await self._checkpoint("creative.script.verified")
        return {
            **payload,
            "draft_script_id": payload["script_id"],
            "script_id": approved_script_id,
            "script_verification": {"blocker_codes": list(result.blocker_codes)},
        }

    @activity.defn(name="salience.creative.direction")
    async def direction(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        request = payload["request"]
        agent_input = {"brief_id": payload["brief"]["brief_id"], "script_id": payload["script_id"]}
        agent_run = await self._state.agents.invoke_by_id("creative_director_agent", agent_input)
        creative_brief_id = await self._state.creative_repository.record_creative_brief(
            workspace_id=request["workspace_id"],
            program_id=request["content_program_id"],
            brief_id=payload["brief"]["brief_id"],
            script_id=payload["script_id"],
            creative_key=f"direction-{payload['script_id']}",
            version=1,
            status="approved",
            creative_plan=agent_run.output,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        storyboard_id = await self._state.creative_repository.record_storyboard(
            creative_brief_id=creative_brief_id,
            version=1,
            status="approved",
            content={"shots": agent_run.output["shots"]},
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        await self._checkpoint("creative.direction.persisted")
        return {
            **payload,
            "creative_brief_id": creative_brief_id,
            "storyboard_id": storyboard_id,
            "direction": agent_run.output,
        }

    @activity.defn(name="salience.creative.authorize")
    async def authorize(self, payload: dict[str, Any]) -> dict[str, Any]:
        denial_reason = None
        if not self._state.permission_granted:
            denial_reason = "permission_denied"
        elif not self._state.policy_allowed:
            denial_reason = "policy_denied"
        elif self._state.estimated_cost_micros > self._state.budget_available_micros:
            denial_reason = "budget_exceeded"
        rights = RightsPolicy().authorize(
            uses_real_likeness=False,
            uses_voice_clone=False,
            consent=None,
            channel="fixture-short-video",
            territory="US",
            commercial_use=False,
        )
        if not rights.allowed:
            denial_reason = rights.reason or "rights_denied"
        await self._checkpoint("creative.authorization.checked")
        return {**payload, "authorized": denial_reason is None, "denial_reason": denial_reason}

    @activity.defn(name="salience.creative.submit_or_reconcile")
    async def submit_or_reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = payload["request"]
        if request["dry_run"]:
            await self._checkpoint("creative.provider.skipped_dry_run")
            return {**payload, "provider_state": "dry_run"}
        run = await self._run()
        capability_request = CreativeCapabilityRequest(
            request_key=f"{request['idempotency_key']}:text-to-video",
            content_program_id=request["content_program_id"],
            brief_id=payload["brief"]["brief_id"],
            script_id=payload["script_id"],
            capability="text_to_video",
            expected_modality="video",
            aspect_ratio="9:16",
            resolution="1080x1920",
            duration_seconds=30,
            max_variants=1,
            provider_extension={"script_text": "Evidence-linked fixture script"},
        )
        creative_job_id = await self._state.creative_repository.record_creative_job(
            workspace_id=request["workspace_id"],
            program_id=request["content_program_id"],
            brief_id=payload["brief"]["brief_id"],
            script_id=payload["script_id"],
            creative_brief_id=payload["creative_brief_id"],
            job_id=str(run.job_id),
            requested_capability=capability_request.capability,
            request_fingerprint=_fingerprint(capability_request.model_dump()),
            idempotency_key=capability_request.request_key,
            request=capability_request.model_dump(),
            timeout_seconds=60,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        existing = await self._state.creative_repository.provider_job_for_creative(
            creative_job_id=creative_job_id, provider_id=self._state.provider.provider_id
        )
        await self._state.store.plan_effect(run, capability_request.request_key)
        if existing is not None and existing.get("external_job_id"):
            provider_result = await self._state.provider.get_status(existing["external_job_id"])
            reconciled = True
        elif await self._state.store.begin_effect_submission(run, capability_request.request_key):
            provider_result = await self._state.provider.submit(capability_request)
            reconciled = False
            if self._state.crash_at == "provider.submitted" and not self._state.crashed:
                self._state.crashed = True
                self._state.crash_reached.set()
                await asyncio.Event().wait()
        else:
            provider_result = await self._state.provider.reconcile(capability_request.request_key)
            if provider_result is None:
                raise RuntimeError(
                    "creative provider submission is ambiguous and cannot be reconciled"
                )
            reconciled = True
        provider_job_id = await self._state.creative_repository.record_provider_job(
            creative_job_id=creative_job_id,
            provider_id=provider_result.provider_id,
            provider_version=provider_result.provider_version,
            model_id=provider_result.model_id,
            external_job_id=provider_result.external_job_id,
            state=provider_result.state,
            normalized_request=capability_request.model_dump(),
            reconciliation_state=provider_result.metadata,
            estimated_cost_micros=self._state.estimated_cost_micros,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        await self._state.store.complete_effect(
            run,
            idempotency_key=capability_request.request_key,
            external_id=provider_result.external_job_id,
            reconciled=reconciled,
        )
        await self._checkpoint("creative.provider.reconciled")
        return {
            **payload,
            "creative_job_id": creative_job_id,
            "provider_job_id": provider_job_id,
            "external_job_id": provider_result.external_job_id,
            "provider_state": provider_result.state,
        }

    @activity.defn(name="salience.creative.await_provider")
    async def await_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("provider_state") == "dry_run":
            return payload
        result = await self._state.provider.get_status(payload["external_job_id"])
        if result.state == "running":
            result = await self._state.provider.get_status(payload["external_job_id"])
        if result.state != "completed":
            raise RuntimeError(f"creative provider ended in {result.state}")
        await self._checkpoint("creative.provider.completed")
        return {**payload, "provider_state": result.state}

    @activity.defn(name="salience.creative.import_validate")
    async def import_validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("provider_state") == "dry_run":
            return payload
        if self._state.media is None:
            raise RuntimeError("creative media engine is not configured")
        run = await self._run()
        data = await self._state.provider.download(payload["external_job_id"])
        receipt = await self._state.media.store_download(data, media_type="video/mp4")
        asset = await self._state.creative_repository.record_asset_variant(
            workspace_id=payload["request"]["workspace_id"],
            program_id=payload["request"]["content_program_id"],
            creative_job_id=payload["creative_job_id"],
            provider_job_id=payload["provider_job_id"],
            storage_key=receipt.storage_key,
            content_hash=receipt.content_hash,
            media_type=receipt.media_type,
            byte_size=receipt.byte_size,
            origin_type="generated",
            variant_key="primary",
            selection_state="selected",
            selection_reason="single bounded fixture variant",
            trace_id=run.trace_context.trace_id,
            technical_properties={"inspection": "not_run"},
        )
        await self._checkpoint("creative.asset.imported")
        return {**payload, "asset_id": asset.asset_id, "asset_variant_id": asset.asset_variant_id}

    @activity.defn(name="salience.creative.distribute")
    async def distribute(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("provider_state") == "dry_run":
            return payload
        package = await CreativeService(self._state.creative_repository).build_distribution(
            _approved_production(payload)
        )
        await self._checkpoint("creative.distribution.persisted")
        return {
            **payload,
            "distribution": package.model_dump(),
            "platform_profile_id": package.platform_profile_id,
            "distribution_package_id": package.distribution_package_id,
        }

    @activity.defn(name="salience.creative.final_gate")
    async def final_gate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("provider_state") == "dry_run":
            return payload
        ready = await CreativeService(self._state.creative_repository).finalize_ready_package(
            _approved_production(payload),
            distribution=DistributionPackage.model_validate(payload["distribution"]),
        )
        await self._checkpoint("creative.final_gate.passed")
        return {**payload, "ready_package_id": ready.ready_package_id}

    @activity.defn(name="salience.creative.complete")
    async def complete(self, payload: dict[str, Any]) -> CreativeProductionResult:
        run = await self._run()
        state = "dry_run_completed" if payload.get("provider_state") == "dry_run" else "completed"
        result = CreativeProductionResult(
            state=state,
            job_id=str(run.job_id),
            trace_id=run.trace_context.trace_id,
            ready_package_id=payload.get("ready_package_id"),
            script_id=payload.get("script_id"),
            asset_id=payload.get("asset_id"),
            provider_job_id=payload.get("provider_job_id"),
            provider_submit_count=getattr(self._state.provider, "submit_count", 0),
        )
        await self._state.store.complete_with_output(run, status="succeeded", output=_result_payload(result))
        return result

    @activity.defn(name="salience.creative.denied")
    async def denied(self, payload: dict[str, Any]) -> CreativeProductionResult:
        run = await self._run()
        await self._checkpoint("creative.denied")
        await self._state.store.terminal(run, "denied")
        return CreativeProductionResult(
            state="denied",
            job_id=str(run.job_id),
            trace_id=run.trace_context.trace_id,
            denial_reason=payload.get("denial_reason"),
        )

    @activity.defn(name="salience.creative.cancel")
    async def cancel(self) -> CreativeProductionResult:
        run = await self._run()
        await self._checkpoint("creative.cancelled")
        await self._state.store.terminal(run, "cancelled")
        return CreativeProductionResult("cancelled", str(run.job_id), run.trace_context.trace_id)


@workflow.defn(name=CREATIVE_WORKFLOW_TYPE)
class CreativeProductionWorkflow:
    def __init__(self) -> None:
        self._cancellation_requested = False

    @workflow.signal
    def request_cancellation(self) -> None:
        self._cancellation_requested = True

    @workflow.run
    async def run(self, request: CreativeProductionRequest) -> CreativeProductionResult:
        payload = await _execute_stage("salience.creative.load_brief", request)
        for activity_name in (
            "salience.creative.script",
            "salience.creative.verify_script",
            "salience.creative.direction",
            "salience.creative.authorize",
        ):
            if self._cancellation_requested:
                return await _execute_terminal("salience.creative.cancel")
            payload = await _execute_stage(activity_name, payload)
        if not payload["authorized"]:
            return await workflow.execute_activity(
                "salience.creative.denied", payload, start_to_close_timeout=timedelta(seconds=10)
            )
        for activity_name in (
            "salience.creative.submit_or_reconcile",
            "salience.creative.await_provider",
            "salience.creative.import_validate",
            "salience.creative.distribute",
            "salience.creative.final_gate",
        ):
            if self._cancellation_requested:
                return await _execute_terminal("salience.creative.cancel")
            payload = await _execute_stage(activity_name, payload)
        return await workflow.execute_activity(
            "salience.creative.complete", payload, start_to_close_timeout=timedelta(seconds=10)
        )


async def _execute_stage(name: str, argument: Any) -> dict[str, Any]:
    return await workflow.execute_activity(
        name,
        argument,
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(milliseconds=100),
            maximum_interval=timedelta(seconds=1),
            maximum_attempts=MAX_CREATIVE_ATTEMPTS,
        ),
    )


async def _execute_terminal(name: str) -> CreativeProductionResult:
    return await workflow.execute_activity(name, start_to_close_timeout=timedelta(seconds=10))


def build_creative_worker(client: Any, *, task_queue: str, state: CreativeWorkflowState) -> Worker:
    activities = CreativeActivities(state)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[CreativeProductionWorkflow],
        activities=[
            activities.load_brief,
            activities.script,
            activities.verify_script,
            activities.direction,
            activities.authorize,
            activities.submit_or_reconcile,
            activities.await_provider,
            activities.import_validate,
            activities.distribute,
            activities.final_gate,
            activities.complete,
            activities.denied,
            activities.cancel,
        ],
    )


def _request_payload(request: CreativeProductionRequest) -> dict[str, Any]:
    return {
        "workspace_id": request.workspace_id,
        "content_program_id": request.content_program_id,
        "brief_id": request.brief_id,
        "idempotency_key": request.idempotency_key,
        "dry_run": request.dry_run,
        "target_profile_key": request.target_profile_key,
        "target_profile_version": request.target_profile_version,
    }


def _approved_production(payload: dict[str, Any]) -> ApprovedProduction:
    request = payload["request"]
    asset_id = payload["asset_id"]
    script_id = payload["script_id"]
    return ApprovedProduction(
        workspace_id=request["workspace_id"],
        content_program_id=request["content_program_id"],
        brief_id=payload["brief"]["brief_id"],
        script_id=script_id,
        asset_id=asset_id,
        profile_key=request["target_profile_key"],
        profile_version=request["target_profile_version"],
        profile_rules={
            "target_platform": "fixture-platform",
            "aspect_ratio": "9:16",
            "caption_limit": 100,
            "allowed_locales": ["en"],
            "requires_disclosure": True,
        },
        title_candidates=[
            {
                "key": "primary",
                "title": "Evidence-linked fixture media",
                "thumbnail_asset_id": asset_id,
                "narrative_fingerprint": script_id,
                "thumbnail_fingerprint": asset_id,
                "template_key": request["target_profile_key"],
            }
        ],
        selected_title_key="primary",
        caption="Evidence-linked fixture media.",
        aspect_ratio="9:16",
        duration_seconds=30,
        locale="en",
        claim_ids=payload["brief"]["claim_ids"],
        generated=True,
        approval_state="approved",
        c2pa_status="not_configured",
    )


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _stage_run_id(run: CanonicalRun, stage: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"salience:creative:{run.job_id}:{stage}"))


def _result_payload(result: CreativeProductionResult) -> dict[str, object]:
    return {
        "state": result.state,
        "ready_package_id": result.ready_package_id,
        "script_id": result.script_id,
        "asset_id": result.asset_id,
        "provider_job_id": result.provider_job_id,
        "provider_submit_count": result.provider_submit_count,
        "denial_reason": result.denial_reason,
    }
