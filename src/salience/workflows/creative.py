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
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from salience.agents.execution import AgentInvocation, AgentService
from salience.creative.contracts import CreativeCapabilityRequest, CreativeVariantPlan, DistributionPackage
from salience.creative.capabilities import CreativeCapabilityRegistry
from salience.creative.governance import RightsPolicy
from salience.creative.media import MediaEngine
from salience.creative.repository import CreativeRepository
from salience.creative.scripts import ScriptVerifier
from salience.creative.service import ApprovedProduction, CreativeService
from salience.governance.cost_repository import CostReservationRepository
from salience.governance.costs import BudgetExceeded, CostSettlementStatus
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.persistence import CanonicalJobStore, CanonicalRun

with workflow.unsafe.imports_passed_through():
    from salience.creative.providers import CreativeProvider, FixtureCreativeProvider
    from salience.creative.providers import ProviderResponseError


CREATIVE_WORKFLOW_TYPE = "CreativeProductionWorkflow"
MAX_CREATIVE_ATTEMPTS = 3
PROVIDER_POLL_INTERVAL_SECONDS = 0.05
PROVIDER_POLLS_PER_TIMEOUT_SECOND = 1


@dataclass(frozen=True)
class CreativeProductionRequest:
    workspace_id: str
    content_program_id: str
    brief_id: str
    idempotency_key: str
    dry_run: bool = True
    budget_id: str | None = None
    target_profile_key: str = "fixture-short-video"
    target_profile_version: int = 1
    max_variants: int = 1
    contract_version: str = "CreativeProductionRequest@v1"

    def __post_init__(self) -> None:
        if self.contract_version != "CreativeProductionRequest@v1":
            raise ValueError("unsupported creative production request contract")
        if not self.workspace_id or not self.content_program_id or not self.brief_id:
            raise ValueError("workspace, content program, and brief identities are required")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        if not self.dry_run and not self.budget_id:
            raise ValueError("non-dry creative runs require a budget identity")
        if self.target_profile_version <= 0:
            raise ValueError("target profile version must be positive")
        if not 1 <= self.max_variants <= 3:
            raise ValueError("max_variants must be between 1 and 3")


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
    provider_registry: CreativeCapabilityRegistry | None = None
    provider_adapters: dict[str, CreativeProvider] = field(default_factory=dict)
    media: MediaEngine | None = None
    cost_repository: CostReservationRepository | None = None
    provider_timeout_seconds: int = 300
    permission_granted: bool = True
    policy_allowed: bool = True
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

    def _select_provider(self, request: CreativeCapabilityRequest) -> tuple[CreativeProvider, str | None]:
        if self._state.provider_registry is None:
            return self._state.provider, None
        manifest = self._state.provider_registry.resolve(request).selected
        provider = self._state.provider_adapters.get(manifest.plugin_id)
        if provider is None:
            raise RuntimeError(f"selected creative provider adapter is unavailable: {manifest.plugin_id}")
        return provider, f"{manifest.plugin_id}@{manifest.version}"

    def _provider_for(self, payload: dict[str, Any]) -> CreativeProvider:
        selected_plugin_id = payload.get("selected_provider_plugin_id")
        if not isinstance(selected_plugin_id, str):
            return self._state.provider
        try:
            return self._state.provider_adapters[selected_plugin_id]
        except KeyError as error:
            raise RuntimeError(f"selected creative provider adapter is unavailable: {selected_plugin_id}") from error

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
        director_run_id = _stage_run_id(run, "creative_director")
        await self._state.intelligence_repository.record_agent_run(
            run_id=director_run_id,
            workspace_id=request["workspace_id"],
            program_id=request["content_program_id"],
            job_id=str(run.job_id),
            manifest=self._state.agents.describe_agent("creative_director_agent"),
            input_payload=agent_input,
            output_payload=agent_run.output,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        capability = agent_run.output["capability_requests"][0]["capability"]
        production_input = {
            "brief_id": payload["brief"]["brief_id"],
            "script_id": payload["script_id"],
            "capability": capability,
            "max_variants": request["max_variants"],
        }
        production_run = await self._state.agents.invoke_by_id("production_agent", production_input)
        production_run_id = _stage_run_id(run, "production")
        await self._state.intelligence_repository.record_agent_run(
            run_id=production_run_id,
            workspace_id=request["workspace_id"],
            program_id=request["content_program_id"],
            job_id=str(run.job_id),
            manifest=self._state.agents.describe_agent("production_agent"),
            input_payload=production_input,
            output_payload=production_run.output,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
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
            "production": production_run.output,
        }

    @activity.defn(name="salience.creative.authorize")
    async def authorize(self, payload: dict[str, Any]) -> dict[str, Any]:
        denial_reason = None
        if not self._state.permission_granted:
            denial_reason = "permission_denied"
        elif not self._state.policy_allowed:
            denial_reason = "policy_denied"
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
        request = payload["request"]
        if denial_reason is None and not request["dry_run"]:
            if self._state.cost_repository is None:
                denial_reason = "cost_infrastructure_unavailable"
            else:
                run = await self._run()
                capability_request = _capability_request(payload)
                provider, selected_manifest = self._select_provider(capability_request)
                variants: list[dict[str, Any]] = []
                try:
                    for variant_request in _variant_requests(capability_request):
                        creative_job_id = await self._state.creative_repository.record_creative_job(
                            workspace_id=request["workspace_id"],
                            program_id=request["content_program_id"],
                            brief_id=payload["brief"]["brief_id"],
                            script_id=payload["script_id"],
                            creative_brief_id=payload["creative_brief_id"],
                            job_id=str(run.job_id),
                            requested_capability=variant_request.capability,
                            request_fingerprint=_fingerprint(variant_request.model_dump()),
                            idempotency_key=variant_request.request_key,
                            request=variant_request.model_dump(),
                            timeout_seconds=60,
                            trace_id=run.trace_context.trace_id,
                            span_id=run.trace_context.span_id,
                        )
                        await self._state.store.plan_effect(run, variant_request.request_key)
                        effect = await self._state.store.effect(run, variant_request.request_key)
                        if effect is None:
                            raise RuntimeError("canonical creative effect was not persisted")
                        reservation = await self._state.cost_repository.reserve_for_effect(
                            budget_id=request["budget_id"],
                            reservation_key=variant_request.request_key,
                            job_id=str(run.job_id),
                            external_effect_id=effect.effect_id,
                            creative_job_id=creative_job_id,
                            estimated_micros=provider.capabilities.estimated_cost_micros,
                        )
                        variants.append(
                            {
                                "variant_key": variant_request.provider_extension["variant_key"],
                                "capability_request": variant_request.model_dump(),
                                "creative_job_id": creative_job_id,
                                "external_effect_id": effect.effect_id,
                                "budget_reservation_id": reservation.reservation_id,
                            }
                        )
                except (BudgetExceeded, KeyError):
                    for variant in variants:
                        await self._state.cost_repository.release_unused(
                            variant["budget_reservation_id"]
                        )
                    denial_reason = "budget_exceeded"
                else:
                    payload = _with_variants(
                        payload,
                        variants,
                        selected_provider_plugin_id=(
                            selected_manifest.split("@", 1)[0] if selected_manifest else None
                        ),
                        selected_provider_manifest=selected_manifest,
                    )
                    await self._checkpoint("creative.budget.reserved")
        await self._checkpoint("creative.authorization.checked")
        return {**payload, "authorized": denial_reason is None, "denial_reason": denial_reason}

    @activity.defn(name="salience.creative.submit_or_reconcile")
    async def submit_or_reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = payload["request"]
        if request["dry_run"]:
            await self._checkpoint("creative.provider.skipped_dry_run")
            return {**payload, "provider_state": "dry_run"}
        run = await self._run()
        provider = self._provider_for(payload)
        variants = [
            await self._submit_variant(payload, variant, provider, run)
            for variant in payload["variants"]
        ]
        await self._checkpoint("creative.provider.reconciled")
        return _with_variants(payload, variants)

    async def _submit_variant(
        self,
        payload: dict[str, Any],
        variant: dict[str, Any],
        provider: CreativeProvider,
        run: CanonicalRun,
    ) -> dict[str, Any]:
        capability_request = CreativeCapabilityRequest.model_validate(variant["capability_request"])
        existing = await self._state.creative_repository.provider_job_for_creative(
            creative_job_id=variant["creative_job_id"], provider_id=provider.provider_id
        )
        if existing is not None and existing.get("external_job_id"):
            provider_result = await provider.get_status(existing["external_job_id"])
            reconciled = True
        elif await self._state.store.begin_effect_submission(run, capability_request.request_key):
            provider_result = await provider.submit(capability_request)
            reconciled = False
            if self._state.crash_at == "provider.submitted" and not self._state.crashed:
                self._state.crashed = True
                self._state.crash_reached.set()
                await asyncio.Event().wait()
        else:
            provider_result = await provider.reconcile(capability_request.request_key)
            if provider_result is None:
                raise RuntimeError(
                    "creative provider submission is ambiguous and cannot be reconciled"
                )
            reconciled = True
        provider_job_id = await self._state.creative_repository.record_provider_job(
            creative_job_id=variant["creative_job_id"],
            provider_id=provider_result.provider_id,
            provider_version=provider_result.provider_version,
            model_id=provider_result.model_id,
            external_job_id=provider_result.external_job_id,
            state=provider_result.state,
            normalized_request=capability_request.model_dump(),
            reconciliation_state=provider_result.metadata,
            estimated_cost_micros=provider_result.usage.estimated_micros,
            actual_cost_micros=provider_result.usage.actual_micros,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        if self._state.cost_repository is None:
            raise RuntimeError("durable cost repository is required for creative submission")
        await self._state.cost_repository.attach_provider_job(
            reservation_id=variant["budget_reservation_id"], provider_job_id=provider_job_id
        )
        await self._state.store.complete_effect(
            run,
            idempotency_key=capability_request.request_key,
            external_id=provider_result.external_job_id,
            reconciled=reconciled,
        )
        return {
            **variant,
            "provider_job_id": provider_job_id,
            "external_job_id": provider_result.external_job_id,
            "provider_state": provider_result.state,
        }

    @activity.defn(name="salience.creative.await_provider")
    async def await_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("provider_state") == "dry_run":
            return payload
        if self._state.cost_repository is None:
            raise RuntimeError("durable cost repository is required for creative settlement")
        run = await self._run()
        provider = self._provider_for(payload)
        variants = [
            await self._await_provider_variant(variant, provider, run)
            for variant in payload["variants"]
        ]
        provider_state = (
            "completed"
            if all(variant["provider_state"] == "completed" for variant in variants)
            else "running"
        )
        if provider_state == "completed":
            await self._checkpoint("creative.cost.settled")
            await self._checkpoint("creative.provider.completed")
        return _with_variants(payload, variants, provider_state=provider_state)

    async def _await_provider_variant(
        self, variant: dict[str, Any], provider: CreativeProvider, run: CanonicalRun
    ) -> dict[str, Any]:
        poll_count = int(variant.get("provider_poll_count", 0)) + 1
        try:
            result = await provider.get_status(variant["external_job_id"])
        except ProviderResponseError as error:
            retry_after_seconds = _retry_after_seconds(error.retry_after)
            await self._state.creative_repository.transition_provider_job(
                provider_job_id=variant["provider_job_id"],
                state="running",
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
                failure_class=error.failure_class,
                retry_after_seconds=retry_after_seconds,
            )
            raise
        if result.state in {"submitted", "running"}:
            if poll_count > self._state.provider_timeout_seconds * PROVIDER_POLLS_PER_TIMEOUT_SECOND:
                await self._dead_letter_provider(
                    variant,
                    run,
                    failure_class="provider_timeout",
                    message="creative provider did not complete before timeout",
                )
                raise ApplicationError("creative provider timed out", non_retryable=True)
            await self._state.creative_repository.transition_provider_job(
                provider_job_id=variant["provider_job_id"],
                state=result.state,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
                next_poll_after_seconds=1,
            )
            return {**variant, "provider_state": result.state, "provider_poll_count": poll_count}
        if result.state != "completed":
            await self._retry_or_dead_letter_provider(
                variant,
                run,
                failure_class=result.failure_class or "provider_terminal_failure",
                message=f"creative provider ended in {result.state}",
            )
            if activity.info().attempt >= MAX_CREATIVE_ATTEMPTS:
                raise ApplicationError(
                    f"creative provider ended in {result.state}", non_retryable=True
                )
            raise RuntimeError(f"creative provider ended in {result.state}")
        await self._state.creative_repository.transition_provider_job(
            provider_job_id=variant["provider_job_id"],
            state="completed",
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
            actual_cost_micros=result.usage.actual_micros,
        )
        settlement = await self._state.cost_repository.record_actual_usage(
            variant["budget_reservation_id"], result.usage.actual_micros
        )
        if settlement.status != CostSettlementStatus.SETTLED:
            raise RuntimeError(
                f"creative provider cost cannot pass finalization: {settlement.status.value}"
            )
        return {
            **variant,
            "provider_state": result.state,
            "cost_status": settlement.status.value,
        }

    async def _retry_or_dead_letter_provider(
        self, variant: dict[str, Any], run: CanonicalRun, *, failure_class: str, message: str
    ) -> None:
        if activity.info().attempt < MAX_CREATIVE_ATTEMPTS:
            await self._state.creative_repository.transition_provider_job(
                provider_job_id=variant["provider_job_id"],
                state="running",
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
                failure_class=failure_class,
            )
            return
        await self._state.creative_repository.transition_provider_job(
            provider_job_id=variant["provider_job_id"],
            state="dead_lettered",
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
            failure_class=failure_class,
        )
        await self._state.store.dead_letter(
            run,
            attempt=activity.info().attempt,
            error_type=failure_class,
            error_message=message,
        )

    async def _dead_letter_provider(
        self, variant: dict[str, Any], run: CanonicalRun, *, failure_class: str, message: str
    ) -> None:
        await self._state.creative_repository.transition_provider_job(
            provider_job_id=variant["provider_job_id"],
            state="dead_lettered",
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
            failure_class=failure_class,
        )
        await self._state.store.dead_letter(
            run,
            attempt=MAX_CREATIVE_ATTEMPTS,
            error_type=failure_class,
            error_message=message,
        )

    @activity.defn(name="salience.creative.import_validate")
    async def import_validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("provider_state") == "dry_run":
            return payload
        if self._state.media is None:
            raise RuntimeError("creative media engine is not configured")
        run = await self._run()
        provider = self._provider_for(payload)
        variants: list[dict[str, Any]] = []
        for index, variant in enumerate(payload["variants"], start=1):
            data = await provider.download(variant["external_job_id"])
            inspection = self._state.media.inspect_bytes(data)
            if inspection.status != "valid" or not inspection.properties.get("video_codec"):
                raise RuntimeError(
                    f"creative media technical validation failed: {inspection.reason or inspection.status}"
                )
            receipt = await self._state.media.store_download(data, media_type="video/mp4")
            selected = index == 1
            asset = await self._state.creative_repository.record_asset_variant(
                workspace_id=payload["request"]["workspace_id"],
                program_id=payload["request"]["content_program_id"],
                creative_job_id=variant["creative_job_id"],
                provider_job_id=variant["provider_job_id"],
                storage_key=receipt.storage_key,
                content_hash=receipt.content_hash,
                media_type=receipt.media_type,
                byte_size=receipt.byte_size,
                origin_type="generated",
                variant_key=variant["variant_key"],
                selection_state="selected" if selected else "rejected",
                selection_reason=(
                    "deterministic_primary_variant"
                    if selected
                    else "not_selected_after_deterministic_primary_selection"
                ),
                trace_id=run.trace_context.trace_id,
                technical_properties=dict(inspection.properties),
            )
            await self._state.creative_repository.record_asset_provenance(
                asset_id=asset.asset_id,
                origin_type="generated",
                validation_status="not_configured",
                c2pa_manifest_reference=None,
                signer_metadata={},
                ingredients=[],
                transformations=[{"provider_job_id": variant["provider_job_id"]}],
            )
            variants.append(
                {
                    **variant,
                    "asset_id": asset.asset_id,
                    "asset_variant_id": asset.asset_variant_id,
                }
            )
        await self._checkpoint("creative.asset.imported")
        return _with_variants(payload, variants)

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
            provider_submit_count=getattr(self._provider_for(payload), "submit_count", 0),
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
    async def cancel(self, payload: dict[str, Any] | None = None) -> CreativeProductionResult:
        run = await self._run()
        if payload is not None and not payload["request"]["dry_run"]:
            await self._cancel_external_production(payload, run)
        await self._checkpoint("creative.cancelled")
        await self._state.store.terminal(run, "cancelled")
        return CreativeProductionResult("cancelled", str(run.job_id), run.trace_context.trace_id)

    async def _cancel_external_production(
        self, payload: dict[str, Any], run: CanonicalRun
    ) -> None:
        if self._state.cost_repository is None:
            raise RuntimeError("durable cost repository is required for creative cancellation")
        variants = payload.get("variants") or [payload]
        for variant in variants:
            await self._cancel_variant(variant, payload, run)

    async def _cancel_variant(
        self, variant: dict[str, Any], payload: dict[str, Any], run: CanonicalRun
    ) -> None:
        reservation_id = variant.get("budget_reservation_id")
        provider_job_id = variant.get("provider_job_id")
        external_job_id = variant.get("external_job_id")
        if isinstance(provider_job_id, str) and isinstance(external_job_id, str):
            await self._state.creative_repository.request_provider_cancellation(
                provider_job_id=provider_job_id,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
            provider = self._provider_for(payload)
            current = await provider.get_status(external_job_id)
            if current.state not in {"completed", "failed", "cancelled"}:
                if provider.capabilities.cancellation_support:
                    current = await provider.cancel(external_job_id)
                else:
                    raise RuntimeError("selected creative provider does not support cancellation")
            await self._state.creative_repository.transition_provider_job(
                provider_job_id=provider_job_id,
                state=current.state,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
                actual_cost_micros=current.usage.actual_micros,
                failure_class=current.failure_class,
            )
            if isinstance(reservation_id, str):
                await self._state.cost_repository.record_actual_usage(
                    reservation_id, current.usage.actual_micros
                )
        elif isinstance(reservation_id, str):
            await self._state.cost_repository.release_unused(reservation_id)


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
                return await _execute_terminal("salience.creative.cancel", payload)
            payload = await _execute_stage(activity_name, payload)
        if not payload["authorized"]:
            return await workflow.execute_activity(
                "salience.creative.denied", payload, start_to_close_timeout=timedelta(seconds=10)
            )
        if self._cancellation_requested:
            return await _execute_terminal("salience.creative.cancel", payload)
        payload = await _execute_stage("salience.creative.submit_or_reconcile", payload)
        while payload.get("provider_state") in {"submitted", "running"}:
            if self._cancellation_requested:
                return await _execute_terminal("salience.creative.cancel", payload)
            payload = await _execute_stage("salience.creative.await_provider", payload)
            if payload.get("provider_state") in {"submitted", "running"}:
                await workflow.sleep(timedelta(seconds=PROVIDER_POLL_INTERVAL_SECONDS))
        for activity_name in (
            "salience.creative.import_validate",
            "salience.creative.distribute",
            "salience.creative.final_gate",
        ):
            if self._cancellation_requested:
                return await _execute_terminal("salience.creative.cancel", payload)
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


async def _execute_terminal(
    name: str, payload: dict[str, Any] | None = None
) -> CreativeProductionResult:
    return await workflow.execute_activity(
        name, payload, start_to_close_timeout=timedelta(seconds=10)
    )


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
        "budget_id": request.budget_id,
        "target_profile_key": request.target_profile_key,
        "target_profile_version": request.target_profile_version,
        "max_variants": request.max_variants,
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


def _capability_request(payload: dict[str, Any]) -> CreativeCapabilityRequest:
    request = payload["request"]
    return CreativeCapabilityRequest(
        request_key=f"{request['idempotency_key']}:text-to-video",
        content_program_id=request["content_program_id"],
        brief_id=payload["brief"]["brief_id"],
        script_id=payload["script_id"],
        capability=payload["production"]["capability"],
        expected_modality="video",
        aspect_ratio="9:16",
        resolution="1080x1920",
        duration_seconds=30,
        max_variants=payload["production"]["requested_variants"],
        provider_extension={"script_text": "Evidence-linked fixture script"},
    )


def _variant_plans(request: CreativeCapabilityRequest) -> tuple[CreativeVariantPlan, ...]:
    return tuple(
        CreativeVariantPlan(
            request_key=f"{request.request_key}:variant:{index}",
            variant_key=f"variant-{index}",
            variant_index=index,
            max_variants=request.max_variants,
        )
        for index in range(1, request.max_variants + 1)
    )


def _variant_requests(request: CreativeCapabilityRequest) -> tuple[CreativeCapabilityRequest, ...]:
    return tuple(
        request.model_copy(
            update={
                "request_key": plan.request_key,
                "max_variants": 1,
                "provider_extension": {
                    **request.provider_extension,
                    "variant_key": plan.variant_key,
                    "variant_index": plan.variant_index,
                },
            }
        )
        for plan in _variant_plans(request)
    )


def _with_variants(
    payload: dict[str, Any], variants: list[dict[str, Any]], **updates: object
) -> dict[str, Any]:
    if not variants:
        raise ValueError("creative production requires at least one variant")
    primary = variants[0]
    primary_fields = {
        key: primary[key]
        for key in (
            "capability_request",
            "creative_job_id",
            "external_effect_id",
            "budget_reservation_id",
            "provider_job_id",
            "external_job_id",
            "provider_state",
            "provider_poll_count",
            "cost_status",
            "asset_id",
            "asset_variant_id",
        )
        if key in primary
    }
    return {**payload, "variants": variants, **primary_fields, **updates}


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


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
