"""Restart-safe, fixture-first Temporal workflow for governed publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from collections.abc import Callable, Mapping
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from salience.governance.cost_repository import CostReservationRepository
from salience.governance.costs import BudgetExceeded, CostSettlementStatus
from salience.publication.contracts import CredentialLease, PublicationRequest, PublisherAdapter
from salience.publication.delivery import PublicationDelivery
from salience.publication.governance import (
    PublicationAuthorizationContext,
    PublicationAuthorizer,
)
from salience.publication.providers import FixturePublisherAdapter
from salience.publication.repository import PublicationRepository
from salience.workflows.persistence import CanonicalJobStore, CanonicalRun
from salience.workflows.schedules import (
    ScheduleRequest,
    TemporalScheduleClient,
    TemporalScheduleService,
)


PUBLICATION_WORKFLOW_TYPE = "GovernedPublicationWorkflow"
MAX_PUBLICATION_ATTEMPTS = 3
PUBLICATION_POLL_INTERVAL_SECONDS = 0.05


@dataclass(frozen=True)
class PublicationWorkflowRequest:
    workspace_id: str
    content_program_id: str
    ready_package_id: str
    publisher_account_id: str
    budget_id: str
    idempotency_key: str
    platform: str = "fixture"
    destination: str = "fixture://account"
    locale: str = "en"
    territory: str = "global"
    visibility: str = "private"
    capability_profile_version: int = 1
    scheduled_publication_request_id: str | None = None
    scheduled_publication_plan_id: str | None = None
    contract_version: str = "PublicationWorkflowRequest@v1"

    def __post_init__(self) -> None:
        if self.contract_version != "PublicationWorkflowRequest@v1":
            raise ValueError("unsupported publication workflow request contract")
        if not all(
            (
                self.workspace_id,
                self.content_program_id,
                self.ready_package_id,
                self.publisher_account_id,
                self.budget_id,
                self.idempotency_key,
            )
        ):
            raise ValueError("publication requires canonical workspace, package, account, budget, and key")
        if self.capability_profile_version <= 0:
            raise ValueError("capability profile version must be positive")
        if (self.scheduled_publication_request_id is None) != (
            self.scheduled_publication_plan_id is None
        ):
            raise ValueError("scheduled publication references must be supplied together")


@dataclass(frozen=True)
class PublicationWorkflowResult:
    publication_state: str
    job_id: str
    trace_id: str
    publication_request_id: str | None = None
    publication_plan_id: str | None = None
    publication_attempt_id: str | None = None
    remote_receipt_id: str | None = None
    publication_id: str | None = None
    fixture_submit_count: int = 0
    denial_reason: str | None = None


@dataclass
class PublicationWorkflowState:
    store: CanonicalJobStore
    repository: PublicationRepository
    provider: FixturePublisherAdapter = field(default_factory=FixturePublisherAdapter)
    publisher_adapters: Mapping[str, PublisherAdapter] = field(default_factory=dict)
    credential_lease_factory: Callable[[PublicationRequest], CredentialLease] | None = None
    delivery: PublicationDelivery = field(
        default_factory=lambda: PublicationDelivery("fixture-publication-delivery-key")
    )
    authorizer: PublicationAuthorizer = field(default_factory=PublicationAuthorizer)
    cost_repository: CostReservationRepository | None = None
    connection_status: str = "active"
    connection_scopes: frozenset[str] = frozenset({"publish:create"})
    policy_allowed: bool = True
    rights_allowed: bool = True
    disclosure_allowed: bool = True
    publishing_approval_state: str = "approved"
    rate_quota_available: bool = True
    emit_duplicate_webhook: bool = True
    max_polls: int = 10
    crash_at: str | None = None
    crash_reached: asyncio.Event = field(default_factory=asyncio.Event)
    crashed: bool = False


@dataclass(frozen=True)
class PublicationScheduleRequest:
    publication_request_id: str
    publication_plan_id: str
    schedule_version: int
    name: str
    every: timedelta
    workflow_request: PublicationWorkflowRequest

    def __post_init__(self) -> None:
        if not all(
            (
                self.publication_request_id,
                self.publication_plan_id,
                self.name,
            )
        ):
            raise ValueError("publication schedule requires immutable request, plan, and name")
        if self.schedule_version <= 0:
            raise ValueError("publication schedule version must be positive")
        if self.every <= timedelta():
            raise ValueError("publication schedule interval must be positive")


class PublicationScheduleService:
    """Delegate timing to Temporal while preserving exact canonical publication inputs."""

    def __init__(self, client: TemporalScheduleClient, *, task_queue: str) -> None:
        self._schedules = TemporalScheduleService(client)
        self._task_queue = task_queue

    async def create_every(self, request: PublicationScheduleRequest) -> str:
        schedule_id = (
            f"publication:{request.publication_request_id}:{request.publication_plan_id}:"
            f"{request.schedule_version}"
        )
        workflow_request = replace(
            request.workflow_request,
            scheduled_publication_request_id=request.publication_request_id,
            scheduled_publication_plan_id=request.publication_plan_id,
        )
        return await self._schedules.create_every(
            ScheduleRequest(
                schedule_id=schedule_id,
                task_queue=self._task_queue,
                every=request.every,
                workflow_type=PUBLICATION_WORKFLOW_TYPE,
                payload=_workflow_request_payload(workflow_request),
            )
        )


class PublicationActivities:
    def __init__(self, state: PublicationWorkflowState) -> None:
        self._state = state

    async def _run(self) -> CanonicalRun:
        return await self._state.store.run_for_workflow(activity.info().workflow_id)

    async def _checkpoint(self, name: str) -> None:
        await self._state.store.checkpoint(await self._run(), name)

    def _adapter_for(self, request: PublicationRequest) -> PublisherAdapter:
        if request.publisher_id is not None:
            configured = self._state.publisher_adapters.get(request.publisher_id)
            if configured is not None:
                return configured
        if self._state.provider.capabilities.publisher_id == request.publisher_id:
            return self._state.provider
        raise RuntimeError(f"no registered publisher adapter for {request.publisher_id}")

    def _lease_for(self, request: PublicationRequest) -> CredentialLease:
        if self._state.credential_lease_factory is not None:
            return self._state.credential_lease_factory(request)
        if request.publisher_id == self._state.provider.capabilities.publisher_id:
            return _fixture_lease()
        raise RuntimeError("publisher credential lease resolver is not configured")

    @activity.defn(name="salience.publication.request")
    async def request(self, workflow_request: PublicationWorkflowRequest) -> dict[str, Any]:
        run = await self._run()
        scheduled_plan: dict[str, str] = {}
        if workflow_request.scheduled_publication_request_id is not None:
            execution = await self._state.repository.load_scheduled_execution(
                workflow_request.scheduled_publication_request_id,
                workflow_request.scheduled_publication_plan_id or "",
            )
            request = execution.request
            if not _workflow_request_matches_publication(workflow_request, request):
                raise ValueError("scheduled publication payload differs from its immutable request")
            scheduled_plan = {
                "publication_plan_id": execution.plan_id,
                "scheduled_publisher_id": execution.publisher_id,
                "scheduled_publisher_version": execution.publisher_version,
            }
        else:
            persisted = await self._state.repository.create_request(
                ready_package_id=workflow_request.ready_package_id,
                workspace_id=workflow_request.workspace_id,
                content_program_id=workflow_request.content_program_id,
                publisher_account_id=workflow_request.publisher_account_id,
                idempotency_key=workflow_request.idempotency_key,
                platform=workflow_request.platform,
                destination=workflow_request.destination,
                locale=workflow_request.locale,
                territory=workflow_request.territory,
                visibility=workflow_request.visibility,
                capability_profile_version=workflow_request.capability_profile_version,
            )
            request = await self._state.repository.load_request(persisted.id)
        await self._checkpoint("publication.request.created")
        return {
            "workflow_request": _workflow_request_payload(workflow_request),
            "request": request.model_dump(mode="json"),
            "publication_request_id": request.id,
            "trace_id": run.trace_context.trace_id,
            "span_id": run.trace_context.span_id,
            **scheduled_plan,
        }

    @activity.defn(name="salience.publication.authorize")
    async def authorize(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        workflow_request = PublicationWorkflowRequest(**payload["workflow_request"])
        request = PublicationRequest.model_validate(payload["request"])
        adapter = self._adapter_for(request)
        effect_key = f"{request.idempotency_key}:publish"
        await self._state.store.plan_effect(run, effect_key)
        effect = await self._state.store.effect(run, effect_key)
        if effect is None:
            raise RuntimeError("publication external effect was not persisted")
        if "publication_plan_id" in payload:
            plan_id = str(payload["publication_plan_id"])
            await self._state.repository.attach_external_effect(
                publication_plan_id=plan_id, external_effect_id=effect.effect_id
            )
        else:
            plan = await self._state.repository.create_plan(
                publication_request_id=request.id,
                version=1,
                publisher_id=request.publisher_id or "fixture-publisher",
                publisher_version=adapter.capabilities.version,
                external_effect_id=effect.effect_id,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
            plan_id = plan.id
        if self._state.cost_repository is None:
            return {**payload, "authorized": False, "denial_reason": "cost_infrastructure_unavailable"}
        try:
            reservation = await self._state.cost_repository.reserve_for_publication_effect(
                budget_id=workflow_request.budget_id,
                reservation_key=effect_key,
                job_id=str(run.job_id),
                external_effect_id=effect.effect_id,
                publication_plan_id=plan_id,
                estimated_micros=0,
            )
        except (BudgetExceeded, KeyError):
            return {**payload, "authorized": False, "denial_reason": "budget_exceeded"}
        await self._state.repository.attach_reservation(
            publication_plan_id=plan_id, budget_reservation_id=reservation.reservation_id
        )
        current = await self._state.repository.load_current_authorization(request.id)
        if current.request != request:
            raise RuntimeError("publication request changed after canonical load")
        if current.profile is None:
            return {
                **payload,
                "authorized": False,
                "denial_reason": "capability_profile_missing",
                "publication_plan_id": plan_id,
                "external_effect_id": effect.effect_id,
                "budget_reservation_id": reservation.reservation_id,
            }
        if (adapter.capabilities.publisher_id, adapter.capabilities.version) != (
            current.profile.publisher_id,
            current.profile.version,
        ):
            return {
                **payload,
                "authorized": False,
                "denial_reason": "registered_publisher_profile_changed",
                "publication_plan_id": plan_id,
                "external_effect_id": effect.effect_id,
                "budget_reservation_id": reservation.reservation_id,
            }
        if "scheduled_publisher_id" in payload and (
            payload["scheduled_publisher_id"], payload["scheduled_publisher_version"]
        ) != (current.profile.publisher_id, current.profile.version):
            return {
                **payload,
                "authorized": False,
                "denial_reason": "scheduled_publisher_profile_changed",
                "publication_plan_id": plan_id,
                "external_effect_id": effect.effect_id,
                "budget_reservation_id": reservation.reservation_id,
            }
        decision = await self._state.authorizer.reauthorize(
            PublicationAuthorizationContext(
                request=request,
                profile=current.profile,
                ready_package_id=request.ready_package_id,
                ready_package_workspace_id=current.request.workspace_id,
                ready_package_program_id=current.request.content_program_id,
                ready_package_approval_state=current.ready_package_approval_state,
                account_workspace_id=current.account_workspace_id,
                connection_account_id=current.connection_account_id or "missing",
                account_type=current.account_type,
                account_status=current.account_status,
                connection_status=current.connection_status or "missing",
                connection_scopes=current.connection_scopes,
                content_type="video",
                authorized_destination=current.request.destination,
                authorized_locale=current.request.locale,
                authorized_territory=current.request.territory,
                authorized_visibility=current.request.visibility,
                policy_allowed=self._state.policy_allowed and current.policy_allowed,
                rights_allowed=self._state.rights_allowed and current.rights_allowed,
                disclosure_allowed=(
                    self._state.disclosure_allowed and current.disclosure_status == "approved"
                ),
                publishing_approval_state=(
                    current.ready_package_approval_state
                    if self._state.publishing_approval_state == "approved"
                    else self._state.publishing_approval_state
                ),
                budget_status=reservation.status.value,
                rate_quota_available=self._state.rate_quota_available,
                capability_profile_version=current.request.capability_profile_version,
            )
        )
        await self._checkpoint("publication.authorization.checked")
        return {
            **payload,
            "authorized": decision.allowed,
            "denial_reason": ",".join(decision.reasons) if decision.reasons else None,
            "publication_plan_id": plan_id,
            "external_effect_id": effect.effect_id,
            "budget_reservation_id": reservation.reservation_id,
        }

    @activity.defn(name="salience.publication.delivery")
    async def delivery(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = PublicationRequest.model_validate(payload["request"])
        capability = self._state.delivery.create(
            asset_id=request.ready_package_id,
            expires_in=timedelta(minutes=5),
        )
        await self._adapter_for(request).prepare_delivery(request, capability.delivery_url)
        await self._checkpoint("publication.delivery.prepared")
        return payload

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def submit_or_reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        request = PublicationRequest.model_validate(payload["request"])
        attempt = await self._state.repository.create_attempt(
            publication_plan_id=payload["publication_plan_id"],
            attempt_number=1,
            idempotency_key=f"{request.idempotency_key}:attempt:1",
        )
        effect_key = f"{request.idempotency_key}:publish"
        submitting = await self._state.store.begin_effect_submission(run, effect_key)
        if submitting:
            receipt = await self._adapter_for(request).submit(request, self._lease_for(request))
            reconciled = False
        else:
            receipt = await self._adapter_for(request).reconcile(request.idempotency_key)
            if receipt is None:
                raise RuntimeError("ambiguous publication outcome requires reconciliation")
            reconciled = True
        remote = await self._state.repository.record_remote_receipt(
            publication_attempt_id=attempt.id,
            publisher_id=receipt.publisher_id,
            remote_id=receipt.remote_id,
            state=receipt.state,
            safe_metadata_hash=receipt.safe_metadata_hash,
            remote_url=receipt.remote_url,
        )
        await self._state.repository.record_status_event(
            publication_attempt_id=attempt.id,
            state=receipt.state,
            source="submission",
            safe_payload_hash=receipt.safe_metadata_hash,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        await self._state.store.complete_effect(
            run,
            idempotency_key=effect_key,
            external_id=receipt.remote_id,
            reconciled=reconciled,
        )
        await self._checkpoint("publication.accepted")
        if self._state.crash_at == "publication.accepted" and not self._state.crashed:
            self._state.crashed = True
            self._state.crash_reached.set()
            await asyncio.Event().wait()
        return {
            **payload,
            "publication_attempt_id": attempt.id,
            "remote_receipt_id": remote.id,
            "remote_id": remote.remote_id,
            "publication_state": receipt.state,
        }

    @activity.defn(name="salience.publication.await")
    async def await_publication(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        request = PublicationRequest.model_validate(payload["request"])
        adapter = self._adapter_for(request)
        remote = await adapter.status(payload["remote_id"])
        if remote is None:
            raise RuntimeError("ambiguous publication status requires reconciliation")
        state = remote.state
        poll_count = int(payload.get("poll_count", 0)) + 1
        if state in {"accepted", "processing"} and poll_count > self._state.max_polls:
            await self._checkpoint("publication.dead_lettered")
            return {
                **payload,
                "publication_state": "dead_lettered",
                "poll_count": poll_count,
                "failure_reason": "publisher_timeout",
            }
        if (
            state == "processing"
            and self._state.emit_duplicate_webhook
            and adapter is self._state.provider
        ):
            signed = self._state.provider.signed_webhook(
                remote, state="processing", delivery_identity=f"{request.idempotency_key}:processing"
            )
            verified = await adapter.verify_webhook(signed)
            status_event = await self._state.repository.record_status_event(
                publication_attempt_id=payload["publication_attempt_id"],
                state=verified.state,
                source="webhook",
                safe_payload_hash=verified.safe_payload_hash,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
            await self._state.repository.record_verified_webhook(
                publication_attempt_id=payload["publication_attempt_id"],
                status_event_id=status_event.id,
                publisher_id=verified.publisher_id,
                delivery_identity=verified.delivery_identity,
                safe_payload_hash=verified.safe_payload_hash,
                state=verified.state,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
            await self._state.repository.record_verified_webhook(
                publication_attempt_id=payload["publication_attempt_id"],
                status_event_id=status_event.id,
                publisher_id=verified.publisher_id,
                delivery_identity=verified.delivery_identity,
                safe_payload_hash=verified.safe_payload_hash,
                state=verified.state,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
        elif state != "accepted":
            await self._state.repository.record_status_event(
                publication_attempt_id=payload["publication_attempt_id"],
                state=state,
                source="poll",
                safe_payload_hash=_safe_hash({"remote_id": remote.remote_id, "state": state}),
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
        if state == "published":
            if self._state.cost_repository is None:
                raise RuntimeError("durable cost repository is required for publication settlement")
            settlement = await self._state.cost_repository.settle(
                payload["budget_reservation_id"], actual_micros=0
            )
            if settlement.status != CostSettlementStatus.SETTLED:
                raise RuntimeError("publication cost cannot pass completion")
            publication = await self._state.repository.record_publication(
                publication_request_id=payload["publication_request_id"],
                remote_receipt_id=payload["remote_receipt_id"],
                state="published",
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
            await self._checkpoint("publication.published")
            return {
                **payload,
                "publication_state": "published",
                "publication_id": publication.id,
                "poll_count": poll_count,
            }
        return {**payload, "publication_state": state, "poll_count": poll_count}

    @activity.defn(name="salience.publication.complete")
    async def complete(self, payload: dict[str, Any]) -> PublicationWorkflowResult:
        run = await self._run()
        result = PublicationWorkflowResult(
            publication_state=payload["publication_state"],
            job_id=str(run.job_id),
            trace_id=run.trace_context.trace_id,
            publication_request_id=payload.get("publication_request_id"),
            publication_plan_id=payload.get("publication_plan_id"),
            publication_attempt_id=payload.get("publication_attempt_id"),
            remote_receipt_id=payload.get("remote_receipt_id"),
            publication_id=payload.get("publication_id"),
            fixture_submit_count=self._state.provider.submit_count,
        )
        await self._state.store.complete_with_output(
            run, status="succeeded", output=_result_payload(result)
        )
        return result

    @activity.defn(name="salience.publication.denied")
    async def denied(self, payload: dict[str, Any]) -> PublicationWorkflowResult:
        run = await self._run()
        await self._checkpoint("publication.denied")
        await self._state.store.terminal(run, "denied")
        return PublicationWorkflowResult(
            publication_state="denied",
            job_id=str(run.job_id),
            trace_id=run.trace_context.trace_id,
            denial_reason=payload.get("denial_reason"),
        )

    @activity.defn(name="salience.publication.cancel")
    async def cancel(self, payload: dict[str, Any] | None = None) -> PublicationWorkflowResult:
        run = await self._run()
        if payload is not None and isinstance(payload.get("remote_id"), str):
            request = PublicationRequest.model_validate(payload["request"])
            receipt = await self._adapter_for(request).cancel(payload["remote_id"])
            if receipt is not None and isinstance(payload.get("publication_attempt_id"), str):
                await self._state.repository.record_status_event(
                    publication_attempt_id=payload["publication_attempt_id"],
                    state="cancelled",
                    source="cancellation",
                    safe_payload_hash=_safe_hash(
                        {"remote_id": receipt.remote_id, "state": "cancelled"}
                    ),
                    trace_id=run.trace_context.trace_id,
                    span_id=run.trace_context.span_id,
                )
        if payload is not None and isinstance(payload.get("budget_reservation_id"), str):
            if self._state.cost_repository is None:
                raise RuntimeError("durable cost repository is required for publication cancellation")
            await self._state.cost_repository.release_unused(payload["budget_reservation_id"])
        await self._checkpoint("publication.cancelled")
        await self._state.store.terminal(run, "cancelled")
        return PublicationWorkflowResult(
            publication_state="cancelled",
            job_id=str(run.job_id),
            trace_id=run.trace_context.trace_id,
            publication_request_id=payload.get("publication_request_id") if payload else None,
            fixture_submit_count=self._state.provider.submit_count,
        )

    @activity.defn(name="salience.publication.dead_letter")
    async def dead_letter(self, payload: dict[str, Any]) -> PublicationWorkflowResult:
        run = await self._run()
        await self._state.store.dead_letter(
            run,
            attempt=MAX_PUBLICATION_ATTEMPTS,
            error_type=str(payload.get("failure_reason", "publication_failure")),
            error_message="governed publication could not reach a terminal provider state",
        )
        return PublicationWorkflowResult(
            publication_state="dead_lettered",
            job_id=str(run.job_id),
            trace_id=run.trace_context.trace_id,
            publication_request_id=payload.get("publication_request_id"),
            publication_plan_id=payload.get("publication_plan_id"),
            publication_attempt_id=payload.get("publication_attempt_id"),
            remote_receipt_id=payload.get("remote_receipt_id"),
            fixture_submit_count=self._state.provider.submit_count,
            denial_reason=payload.get("failure_reason"),
        )


@workflow.defn(name=PUBLICATION_WORKFLOW_TYPE)
class GovernedPublicationWorkflow:
    def __init__(self) -> None:
        self._cancellation_requested = False

    @workflow.signal
    def request_cancellation(self) -> None:
        self._cancellation_requested = True

    @workflow.run
    async def run(self, request: PublicationWorkflowRequest) -> PublicationWorkflowResult:
        payload = await _execute_stage("salience.publication.request", request)
        if self._cancellation_requested:
            return await _execute_terminal("salience.publication.cancel", payload)
        payload = await _execute_stage("salience.publication.authorize", payload)
        if not payload["authorized"]:
            return await workflow.execute_activity(
                "salience.publication.denied", payload, start_to_close_timeout=timedelta(seconds=10)
            )
        if self._cancellation_requested:
            return await _execute_terminal("salience.publication.cancel", payload)
        payload = await _execute_stage("salience.publication.delivery", payload)
        if self._cancellation_requested:
            return await _execute_terminal("salience.publication.cancel", payload)
        payload = await _execute_stage("salience.publication.submit_or_reconcile", payload)
        while payload["publication_state"] in {"accepted", "processing"}:
            if self._cancellation_requested:
                return await _execute_terminal("salience.publication.cancel", payload)
            await workflow.sleep(timedelta(seconds=PUBLICATION_POLL_INTERVAL_SECONDS))
            payload = await _execute_stage("salience.publication.await", payload)
        if payload["publication_state"] == "dead_lettered":
            return await _execute_terminal("salience.publication.dead_letter", payload)
        if payload["publication_state"] != "published":
            return await _execute_terminal("salience.publication.dead_letter", payload)
        return await workflow.execute_activity(
            "salience.publication.complete", payload, start_to_close_timeout=timedelta(seconds=10)
        )


async def _execute_stage(name: str, argument: Any) -> dict[str, Any]:
    return await workflow.execute_activity(
        name,
        argument,
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(milliseconds=100),
            maximum_interval=timedelta(seconds=1),
            maximum_attempts=MAX_PUBLICATION_ATTEMPTS,
        ),
    )


async def _execute_terminal(
    name: str, payload: dict[str, Any] | None = None
) -> PublicationWorkflowResult:
    return await workflow.execute_activity(
        name, payload, start_to_close_timeout=timedelta(seconds=10)
    )


def build_publication_worker(
    client: Any, *, task_queue: str, state: PublicationWorkflowState
) -> Worker:
    activities = PublicationActivities(state)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[GovernedPublicationWorkflow],
        activities=[
            activities.request,
            activities.authorize,
            activities.delivery,
            activities.submit_or_reconcile,
            activities.await_publication,
            activities.complete,
            activities.denied,
            activities.cancel,
            activities.dead_letter,
        ],
    )


def _workflow_request_payload(request: PublicationWorkflowRequest) -> dict[str, object]:
    return {
        "workspace_id": request.workspace_id,
        "content_program_id": request.content_program_id,
        "ready_package_id": request.ready_package_id,
        "publisher_account_id": request.publisher_account_id,
        "budget_id": request.budget_id,
        "idempotency_key": request.idempotency_key,
        "platform": request.platform,
        "destination": request.destination,
        "locale": request.locale,
        "territory": request.territory,
        "visibility": request.visibility,
        "capability_profile_version": request.capability_profile_version,
        "scheduled_publication_request_id": request.scheduled_publication_request_id,
        "scheduled_publication_plan_id": request.scheduled_publication_plan_id,
        "contract_version": request.contract_version,
    }


def _fixture_lease() -> CredentialLease:
    return CredentialLease(
        "fixture-publication-lease",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        granted_scopes=frozenset({"publish:create"}),
    )


def _workflow_request_matches_publication(
    workflow_request: PublicationWorkflowRequest, request: PublicationRequest
) -> bool:
    return (
        workflow_request.workspace_id,
        workflow_request.content_program_id,
        workflow_request.ready_package_id,
        workflow_request.publisher_account_id,
        workflow_request.idempotency_key,
        workflow_request.platform,
        workflow_request.destination,
        workflow_request.locale,
        workflow_request.territory,
        workflow_request.visibility,
        workflow_request.capability_profile_version,
    ) == (
        request.workspace_id,
        request.content_program_id,
        request.ready_package_id,
        request.publisher_account_id,
        request.idempotency_key,
        request.platform,
        request.destination,
        request.locale,
        request.territory,
        request.visibility,
        request.capability_profile_version,
    )


def _safe_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _result_payload(result: PublicationWorkflowResult) -> dict[str, object]:
    return {
        "publication_state": result.publication_state,
        "publication_request_id": result.publication_request_id,
        "publication_plan_id": result.publication_plan_id,
        "publication_attempt_id": result.publication_attempt_id,
        "remote_receipt_id": result.remote_receipt_id,
        "publication_id": result.publication_id,
        "fixture_submit_count": result.fixture_submit_count,
        "denial_reason": result.denial_reason,
    }
