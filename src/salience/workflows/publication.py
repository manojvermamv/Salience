"""Restart-safe, fixture-first Temporal workflow for governed publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from salience.cycles.production_dispatch import (
    ProductionAuthorizationDenied,
    ProductionEffectDispatch,
    PublicationEffectBinding,
    ProductionPermitRequest,
)
from salience.governance.production_policy import ProductionApprovalBinding
from salience.governance.policy import EffectClass
from salience.governance.cost_repository import CostReservationRepository
from salience.governance.costs import BudgetExceeded, CostSettlementStatus
from salience.publication.contracts import CredentialLease, PublicationRequest, PublisherAdapter
from salience.publication.delivery import PublicationDelivery
from salience.publication.governance import (
    PublicationAuthorizationContext,
    PublicationAuthorizer,
)
from salience.publication.providers import FixturePublisherAdapter
from salience.publication.repository import (
    CurrentPublicationAuthorization,
    PublicationRepository,
)
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
    workspace_id: str = ""
    content_program_id: str = ""
    ready_package_id: str = ""
    publisher_account_id: str = ""
    publication_approval_request_id: str = ""
    budget_id: str = ""
    idempotency_key: str = ""
    platform: str = "fixture"
    destination: str = "fixture://account"
    locale: str = "en"
    territory: str = "global"
    visibility: str = "private"
    capability_profile_version: int = 1
    scheduled_publication_schedule_id: str | None = None
    contract_version: str = "PublicationWorkflowRequest@v1"

    def __post_init__(self) -> None:
        if self.contract_version != "PublicationWorkflowRequest@v1":
            raise ValueError("unsupported publication workflow request contract")
        if self.scheduled_publication_schedule_id is not None:
            if not self.scheduled_publication_schedule_id:
                raise ValueError("scheduled publication requires its canonical schedule identity")
            return
        if not all(
            (
                self.workspace_id,
                self.content_program_id,
                self.ready_package_id,
                self.publisher_account_id,
                self.publication_approval_request_id,
                self.budget_id,
                self.idempotency_key,
            )
        ):
            raise ValueError("publication requires canonical workspace, package, account, budget, and key")
        if self.capability_profile_version <= 0:
            raise ValueError("capability profile version must be positive")


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
    production_dispatch: ProductionEffectDispatch | None = None
    production_permit_request_factory: Callable[
        [ProductionApprovalBinding, PublicationEffectBinding], ProductionPermitRequest
    ] | None = None
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
    publication_schedule_id: str
    name: str
    every: timedelta

    def __post_init__(self) -> None:
        if not all(
            (
                self.publication_schedule_id,
                self.name,
            )
        ):
            raise ValueError("publication schedule requires immutable request, plan, and name")
        if self.every <= timedelta():
            raise ValueError("publication schedule interval must be positive")


class PublicationScheduleService:
    """Delegate timing to Temporal with a canonical schedule identity only."""

    def __init__(self, client: TemporalScheduleClient, *, task_queue: str) -> None:
        self._schedules = TemporalScheduleService(client)
        self._task_queue = task_queue

    async def create_every(self, request: PublicationScheduleRequest) -> str:
        return await self._schedules.create_every(
            ScheduleRequest(
                schedule_id=f"publication:{request.publication_schedule_id}",
                task_queue=self._task_queue,
                every=request.every,
                workflow_type=PUBLICATION_WORKFLOW_TYPE,
                payload=_workflow_request_payload(
                    PublicationWorkflowRequest(
                        scheduled_publication_schedule_id=request.publication_schedule_id
                    )
                ),
            )
        )


class PublicationActivities:
    def __init__(self, state: PublicationWorkflowState) -> None:
        self._state = state

    async def _run(self) -> CanonicalRun:
        info = activity.info()
        if info.workflow_id is None:
            raise RuntimeError("publication activity is missing its workflow identity")
        return await self._state.store.run_for_workflow(info.workflow_id, info.workflow_run_id)

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

    def _is_fixture_adapter(self, request: PublicationRequest) -> bool:
        return (
            isinstance(self._state.provider, FixturePublisherAdapter)
            and self._adapter_for(request) is self._state.provider
        )

    def _production_projection_denial(
        self, current: CurrentPublicationAuthorization, request: PublicationRequest
    ) -> str | None:
        policy = current.production_policy
        binding = current.approval_binding
        now = current.database_time
        if policy is None or binding is None or now is None:
            return "production_authority_missing"
        if current.policy_id != str(policy.id) or current.policy_document_hash != policy.document_hash:
            return "production_policy_binding_mismatch"
        if current.policy_status != "active" or policy.status != "active":
            return "production_policy_inactive"
        if now.tzinfo is None or now.utcoffset() is None or policy.expires_at <= now:
            return "production_policy_expired"
        if EffectClass.WRITE not in policy.allowed_effects:
            return "production_effect_not_allowed"
        if EffectClass.WRITE not in policy.approval_required_effects:
            return "production_approval_requirement_missing"
        if current.publication_approval_state != "approved":
            return "production_approval_not_current"
        if binding.approval_request_id != UUID(request.publication_approval_request_id):
            return "production_approval_binding_mismatch"
        if (
            binding.workspace_id != UUID(request.workspace_id)
            or binding.program_id != UUID(request.content_program_id)
            or binding.account_id != UUID(request.publisher_account_id)
            or binding.provider_id != request.publisher_id
            or binding.publication_request_id != UUID(request.id)
            or binding.policy_version_id != policy.id
            or binding.policy_document_hash != policy.document_hash
        ):
            return "production_approval_binding_mismatch"
        scope = policy.publication_scope
        if scope is None or (
            scope.platform,
            scope.destination,
            scope.locale,
            scope.territory,
            scope.visibility,
        ) != (
            request.platform,
            request.destination,
            request.locale,
            request.territory,
            request.visibility,
        ):
            return "production_publication_scope_mismatch"
        if binding.expires_at <= now:
            return "production_approval_expired"
        if current.budget_status != "reserved":
            return "production_budget_reservation_not_current"
        return None

    async def _load_current_production_authorization(
        self, request: PublicationRequest
    ) -> tuple[CurrentPublicationAuthorization | None, str | None]:
        try:
            current = await self._state.repository.load_current_authorization(request.id)
        except (KeyError, PermissionError, ValueError):
            return None, "canonical_production_authority_invalid"
        if current.request != request:
            return None, "publication_request_changed"
        adapter = self._adapter_for(request)
        if current.profile is None:
            return None, "capability_profile_missing"
        if (adapter.capabilities.publisher_id, adapter.capabilities.version) != (
            current.profile.publisher_id,
            current.profile.version,
        ):
            return None, "registered_publisher_profile_changed"
        projection_denial = self._production_projection_denial(current, request)
        if projection_denial is not None:
            return None, projection_denial
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
                policy_allowed=current.policy_allowed,
                rights_allowed=current.rights_allowed,
                disclosure_allowed=current.disclosure_status == "approved",
                publishing_approval_state=current.publication_approval_state or "missing",
                budget_status=current.budget_status or "missing",
                rate_quota_available=current.profile.quota_state == "available",
                capability_profile_version=current.request.capability_profile_version,
            )
        )
        if not decision.allowed:
            return None, ",".join(decision.reasons) or "publication_authorization_denied"
        return current, None

    @staticmethod
    def _production_publication_target_denial(
        current: CurrentPublicationAuthorization,
        payload: dict[str, Any],
        action_kind: str,
    ) -> str | None:
        binding = current.approval_binding
        if binding is None:
            return "production_approval_binding_missing"
        identifiers = (
            ("publication_request_id", current.request.id, binding.publication_request_id),
            ("publication_plan_id", current.publication_plan_id, binding.publication_plan_id),
            ("publication_attempt_id", current.publication_attempt_id, binding.publication_attempt_id),
            ("external_effect_id", current.external_effect_id, binding.external_effect_id),
        )
        for name, canonical, approved in identifiers:
            if canonical is None or approved is None or str(approved) != str(canonical):
                return "production_publication_binding_mismatch"
            if payload.get(name) is None or str(payload[name]) != str(canonical):
                return "production_publication_target_mismatch"
        if current.job_id is None or binding.job_id is None or str(current.job_id) != str(binding.job_id):
            return "production_publication_binding_mismatch"
        if current.publication_plan_state in {None, "cancelled", "failed", "superseded"}:
            return "production_publication_plan_not_current"
        if current.publication_attempt_state in {None, "cancelled", "failed", "superseded"}:
            return "production_publication_attempt_not_current"
        if current.external_effect_state is None:
            return "production_publication_effect_missing"
        approved_reservation_id = binding.budget_reservation_id
        if (
            current.budget_reservation_id is None
            or approved_reservation_id is None
            or str(current.budget_reservation_id) != str(approved_reservation_id)
            or payload.get("budget_reservation_id") is None
            or str(payload["budget_reservation_id"]) != str(current.budget_reservation_id)
        ):
            return "production_publication_reservation_binding_mismatch"

        if current.remote_receipt_id is None:
            if binding.remote_receipt_id is not None:
                return "production_publication_receipt_binding_mismatch"
            if payload.get("remote_receipt_id") is not None or payload.get("remote_id") is not None:
                return "production_publication_receipt_target_mismatch"
            if action_kind in {"await", "cancel", "finalize"}:
                return "production_publication_receipt_missing"
        else:
            if (
                binding.remote_receipt_id is not None
                and str(binding.remote_receipt_id) != str(current.remote_receipt_id)
            ):
                return "production_publication_receipt_binding_mismatch"
            if payload.get("remote_receipt_id") is None or str(
                payload["remote_receipt_id"]
            ) != str(current.remote_receipt_id):
                return "production_publication_receipt_target_mismatch"
            if current.remote_id is None:
                return "production_publication_remote_target_missing"
            if payload.get("remote_id") is None or str(payload["remote_id"]) != str(
                current.remote_id
            ):
                return "production_publication_remote_target_mismatch"

        if current.publication_id is not None and payload.get("publication_id") is not None:
            if str(payload["publication_id"]) != str(current.publication_id):
                return "production_publication_target_mismatch"
        if action_kind == "finalize" and (
            current.publication_id is None
            or payload.get("publication_id") is None
            or str(payload["publication_id"]) != str(current.publication_id)
            or current.publication_state != "published"
        ):
            return "production_publication_result_not_current"
        return None

    def _canonical_publication_payload(
        self, payload: dict[str, Any], current: CurrentPublicationAuthorization
    ) -> dict[str, Any]:
        canonical = {
            "publication_request_id": current.request.id,
            "publication_plan_id": current.publication_plan_id,
            "publication_attempt_id": current.publication_attempt_id,
            "external_effect_id": current.external_effect_id,
            "budget_reservation_id": current.budget_reservation_id,
        }
        if current.remote_receipt_id is not None:
            canonical["remote_receipt_id"] = current.remote_receipt_id
            canonical["remote_id"] = current.remote_id
        if current.publication_id is not None:
            canonical["publication_id"] = current.publication_id
        return {**payload, **canonical}

    async def _hold_publication_liability_if_claimed(
        self,
        payload: dict[str, Any],
        action_kind: str,
        reason_code: str,
    ) -> bool:
        dispatch = self._state.production_dispatch
        if dispatch is None:
            if action_kind != "delivery":
                raise ApplicationError(
                    "production dispatch is unavailable while publication liability may be claimed",
                    type="ProductionPublicationLiabilityHeld",
                    non_retryable=True,
                )
            return False
        hold = getattr(dispatch, "record_publication_liability_held", None)
        if not callable(hold):
            if action_kind != "delivery":
                raise ApplicationError(
                    "production liability hold writer is unavailable",
                    type="ProductionPublicationLiabilityHeld",
                    non_retryable=True,
                )
            return False
        try:
            request_payload = payload["request"]
            publication_request_id = UUID(str(request_payload["id"]))
            held = await asyncio.to_thread(
                hold,
                publication_request_id,
                action_kind,
                reason_code,
            )
        except (KeyError, TypeError, ValueError, ProductionAuthorizationDenied) as error:
            raise ApplicationError(
                "canonical publication identity is unavailable for liability hold",
                type="ProductionPublicationLiabilityHeld",
                non_retryable=True,
            ) from error
        except Exception as error:
            raise ApplicationError(
                "production publication liability hold could not be persisted",
                type="ProductionPublicationLiabilityHeld",
                non_retryable=True,
            ) from error
        if held:
            raise ApplicationError(
                "production publication liability is held after authorization denial",
                type="ProductionPublicationLiabilityHeld",
                non_retryable=True,
            )
        return False

    async def _production_action_current(
        self, payload: dict[str, Any], action_kind: str
    ) -> tuple[CurrentPublicationAuthorization | None, str | None]:
        request = PublicationRequest.model_validate(payload["request"])
        if self._is_fixture_adapter(request):
            return None, None
        dispatch = self._state.production_dispatch
        if dispatch is None:
            if action_kind != "delivery":
                raise ApplicationError(
                    "production dispatch is unavailable while publication liability may be claimed",
                    type="ProductionPublicationLiabilityHeld",
                    non_retryable=True,
                )
            return None, "production_dispatch_unavailable"
        current, denial = await self._load_current_production_authorization(request)
        if denial is not None or current is None:
            reason = denial or "canonical_production_authority_invalid"
            await self._hold_publication_liability_if_claimed(payload, action_kind, reason)
            return None, reason
        target_denial = self._production_publication_target_denial(
            current, payload, action_kind
        )
        if target_denial is not None:
            await self._hold_publication_liability_if_claimed(
                payload, action_kind, target_denial
            )
            return None, target_denial
        binding = current.approval_binding
        operation_value = payload.get("production_operation_id")
        permit_value = payload.get("production_permit_id")
        if operation_value != str(binding.operation_id) or not isinstance(permit_value, str):
            reason = "production_dispatch_binding_mismatch"
            await self._hold_publication_liability_if_claimed(payload, action_kind, reason)
            return None, reason
        try:
            permit_id = UUID(permit_value)
        except ValueError:
            reason = "production_dispatch_binding_mismatch"
            await self._hold_publication_liability_if_claimed(payload, action_kind, reason)
            return None, reason
        try:
            decision = await asyncio.to_thread(
                dispatch.authorize_action, binding.operation_id, permit_id, action_kind
            )
        except ProductionAuthorizationDenied as error:
            await self._hold_publication_liability_if_claimed(
                payload, action_kind, error.reason_code
            )
            return None, error.reason_code
        if not decision.authorized:
            reason = decision.reason_code or "route_fence_denied"
            await self._hold_publication_liability_if_claimed(payload, action_kind, reason)
            return None, reason
        return current, None

    async def _authorize_production_action(
        self, payload: dict[str, Any], action_kind: str
    ) -> str | None:
        _current, denial = await self._production_action_current(payload, action_kind)
        return denial

    async def _production_target_before_submit(
        self, payload: dict[str, Any], request: PublicationRequest
    ) -> tuple[CurrentPublicationAuthorization | None, str | None]:
        current, denial = await self._load_current_production_authorization(request)
        if denial is not None or current is None:
            reason = denial or "canonical_production_authority_invalid"
            await self._hold_publication_liability_if_claimed(payload, "submit", reason)
            return None, reason
        target_denial = self._production_publication_target_denial(
            current, payload, "submit"
        )
        if target_denial is not None:
            await self._hold_publication_liability_if_claimed(
                payload, "submit", target_denial
            )
            return None, target_denial
        return current, None

    async def _claim_production_permit(
        self,
        payload: dict[str, Any],
        request: PublicationRequest,
        current: CurrentPublicationAuthorization | None = None,
    ) -> str | None:
        dispatch = self._state.production_dispatch
        if dispatch is None:
            return "production_dispatch_unavailable"
        if current is None:
            current, denial = await self._load_current_production_authorization(request)
        else:
            denial = None
        if denial is not None or current is None:
            return denial or "canonical_production_authority_invalid"
        target_denial = self._production_publication_target_denial(
            current, payload, "submit"
        )
        if target_denial is not None:
            return target_denial
        binding = current.approval_binding
        if binding is None:
            return "production_approval_binding_missing"
        operation_value = payload.get("production_operation_id")
        permit_value = payload.get("production_permit_id")
        if operation_value != str(binding.operation_id) or not isinstance(permit_value, str):
            return "production_dispatch_binding_mismatch"
        try:
            permit_id = UUID(permit_value)
        except ValueError:
            return "production_dispatch_binding_mismatch"
        try:
            result = await asyncio.to_thread(
                dispatch.claim_permit, binding.operation_id, permit_id
            )
        except ProductionAuthorizationDenied as error:
            return error.reason_code
        if not result.dispatch_allowed:
            return "production_dispatch_claim_denied"
        return None

    def _production_permit_request(
        self,
        current: CurrentPublicationAuthorization,
        request: PublicationRequest,
        publication_binding: PublicationEffectBinding,
    ) -> ProductionPermitRequest:
        approval = current.approval_binding
        factory = self._state.production_permit_request_factory
        if approval is None or factory is None:
            raise ProductionAuthorizationDenied("invalid_binding")
        proposed = factory(approval, publication_binding)
        if not isinstance(proposed, ProductionPermitRequest):
            raise ProductionAuthorizationDenied("invalid_binding")
        expected = {
            "workspace_id": approval.workspace_id,
            "goal_id": approval.goal_id,
            "goal_revision": approval.goal_revision,
            "program_id": approval.program_id,
            "intent_id": approval.intent_id,
            "cycle_id": approval.cycle_id,
            "context_id": approval.context_id,
            "operation_id": approval.operation_id,
            "subject_id": approval.subject_id,
            "policy_version_id": approval.policy_version_id,
            "approval_request_id": approval.approval_request_id,
            "required_scopes": approval.required_scopes,
            "effect_type": approval.effect_type,
            "purpose": approval.purpose,
            "artifact_sha256": approval.artifact_sha256,
            "account_id": approval.account_id,
            "budget_id": approval.budget_id,
            "budget_reservation_id": approval.budget_reservation_id,
            "provider_id": approval.provider_id,
            "idempotency_key": request.idempotency_key,
            "publication_binding": publication_binding,
        }
        if any(getattr(proposed, field) != value for field, value in expected.items()):
            raise ProductionAuthorizationDenied("invalid_binding")
        return proposed

    def _lease_for(self, request: PublicationRequest) -> CredentialLease:
        if self._state.credential_lease_factory is not None:
            return self._state.credential_lease_factory(request)
        if request.publisher_id == self._state.provider.capabilities.publisher_id:
            return _fixture_lease()
        raise RuntimeError("publisher credential lease resolver is not configured")

    async def _current_authorization_denial(self, request: PublicationRequest) -> str | None:
        current = await self._state.repository.load_current_authorization(request.id)
        if current.request != request:
            return "publication_request_changed"
        adapter = self._adapter_for(request)
        if current.profile is None:
            return "capability_profile_missing"
        if (adapter.capabilities.publisher_id, adapter.capabilities.version) != (
            current.profile.publisher_id,
            current.profile.version,
        ):
            return "registered_publisher_profile_changed"
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
                    current.publication_approval_state
                    if self._state.publishing_approval_state == "approved"
                    else self._state.publishing_approval_state
                ),
                budget_status="reserved",
                rate_quota_available=self._state.rate_quota_available,
                capability_profile_version=current.request.capability_profile_version,
            )
        )
        return ",".join(decision.reasons) if decision.reasons else None

    @activity.defn(name="salience.publication.request")
    async def request(self, workflow_request: PublicationWorkflowRequest) -> dict[str, Any]:
        scheduled_plan: dict[str, str] = {}
        if workflow_request.scheduled_publication_schedule_id is not None:
            execution = await self._state.repository.load_scheduled_execution(
                workflow_request.scheduled_publication_schedule_id
            )
            request = execution.request
            if execution.budget_id is None:
                raise RuntimeError("scheduled publication has no canonical budget")
            info = activity.info()
            if info.workflow_id is None:
                raise RuntimeError("scheduled publication activity is missing its workflow identity")
            temporal_run_id = info.workflow_run_id or info.workflow_id
            run = await self._state.store.create_scheduled_publication_run(
                workflow_run_id=temporal_run_id,
                task_queue=info.task_queue,
                workspace_id=request.workspace_id,
                content_program_id=request.content_program_id,
                ready_package_id=request.ready_package_id,
                publisher_account_id=request.publisher_account_id,
                idempotency_key=(
                    f"publication-schedule:{workflow_request.scheduled_publication_schedule_id}:"
                    f"{temporal_run_id}"
                ),
            )
            workflow_request = PublicationWorkflowRequest(
                workspace_id=request.workspace_id,
                content_program_id=request.content_program_id,
                ready_package_id=request.ready_package_id,
                publisher_account_id=request.publisher_account_id,
                publication_approval_request_id=request.publication_approval_request_id,
                budget_id=execution.budget_id,
                idempotency_key=request.idempotency_key,
                platform=request.platform,
                destination=request.destination,
                locale=request.locale,
                territory=request.territory,
                visibility=request.visibility,
                capability_profile_version=request.capability_profile_version,
            )
            scheduled_plan = {
                "publication_plan_id": execution.plan_id,
                "scheduled_publisher_id": execution.publisher_id,
                "scheduled_publisher_version": execution.publisher_version,
            }
        else:
            run = await self._run()
            persisted = await self._state.repository.create_request(
                ready_package_id=workflow_request.ready_package_id,
                workspace_id=workflow_request.workspace_id,
                content_program_id=workflow_request.content_program_id,
                publisher_account_id=workflow_request.publisher_account_id,
                publication_approval_request_id=workflow_request.publication_approval_request_id,
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
        fixture_adapter = self._is_fixture_adapter(request)
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
        try:
            current = await self._state.repository.load_current_authorization(request.id)
        except (KeyError, PermissionError, ValueError):
            return {
                **payload,
                "authorized": False,
                "denial_reason": "canonical_publication_authority_invalid",
                "publication_plan_id": plan_id,
                "external_effect_id": effect.effect_id,
                "budget_reservation_id": reservation.reservation_id,
            }
        if current.request != request:
            if fixture_adapter:
                raise RuntimeError("publication request changed after canonical load")
            return {
                **payload,
                "authorized": False,
                "denial_reason": "publication_request_changed",
                "publication_plan_id": plan_id,
                "external_effect_id": effect.effect_id,
                "budget_reservation_id": reservation.reservation_id,
            }
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
                policy_allowed=(
                    current.policy_allowed
                    and (self._state.policy_allowed if fixture_adapter else True)
                ),
                rights_allowed=(
                    current.rights_allowed
                    and (self._state.rights_allowed if fixture_adapter else True)
                ),
                disclosure_allowed=(
                    current.disclosure_status == "approved"
                    and (self._state.disclosure_allowed if fixture_adapter else True)
                ),
                publishing_approval_state=(
                    (
                        current.publication_approval_state
                        if self._state.publishing_approval_state == "approved"
                        else self._state.publishing_approval_state
                    )
                    if fixture_adapter
                    else current.publication_approval_state
                ),
                budget_status=reservation.status.value,
                rate_quota_available=(
                    self._state.rate_quota_available
                    if fixture_adapter
                    else current.profile.quota_state == "available"
                ),
                capability_profile_version=current.request.capability_profile_version,
            )
        )
        authorized = decision.allowed
        denial_reason = ",".join(decision.reasons) if decision.reasons else None
        production_permit_payload: dict[str, str] = {}
        production_attempt_id: str | None = None
        if authorized and not fixture_adapter:
            dispatch = self._state.production_dispatch
            if dispatch is None or self._state.production_permit_request_factory is None:
                authorized = False
                denial_reason = "production_dispatch_unavailable"
            else:
                production_current, production_denial = (
                    await self._load_current_production_authorization(request)
                )
                if production_denial is not None or production_current is None:
                    authorized = False
                    denial_reason = production_denial or "canonical_production_authority_invalid"
                else:
                    binding = production_current.approval_binding
                    info = activity.info()
                    if binding is None or info.workflow_id is None or info.workflow_run_id is None:
                        authorized = False
                        denial_reason = "production_approval_binding_missing"
                    elif (
                        binding.publication_request_id != UUID(request.id)
                        or binding.job_id != UUID(str(run.job_id))
                        or binding.workflow_id != info.workflow_id
                        or binding.workflow_run_id != info.workflow_run_id
                        or binding.publication_plan_id != UUID(plan_id)
                        or binding.external_effect_id != UUID(str(effect.effect_id))
                        or production_current.publication_plan_id != str(plan_id)
                        or production_current.external_effect_id != str(effect.effect_id)
                        or production_current.budget_reservation_id != str(reservation.reservation_id)
                        or production_current.job_id != str(run.job_id)
                        or (
                            production_current.publication_attempt_id is not None
                            and production_current.publication_attempt_id
                            != str(binding.publication_attempt_id)
                        )
                        or binding.publication_attempt_id is None
                        or binding.remote_receipt_id is not None
                    ):
                        authorized = False
                        denial_reason = "production_publication_binding_mismatch"
                    else:
                        attempt = await self._state.repository.create_attempt(
                            publication_plan_id=plan_id,
                            attempt_number=1,
                            idempotency_key=f"{request.idempotency_key}:attempt:1",
                        )
                        if str(attempt.id) != str(binding.publication_attempt_id):
                            authorized = False
                            denial_reason = "production_publication_attempt_mismatch"
                        else:
                            production_current, production_denial = (
                                await self._load_current_production_authorization(request)
                            )
                            if production_denial is not None or production_current is None:
                                authorized = False
                                denial_reason = production_denial or "canonical_production_authority_invalid"
                            elif (
                                production_current.publication_plan_id != str(plan_id)
                                or production_current.publication_attempt_id != str(attempt.id)
                                or production_current.external_effect_id != str(effect.effect_id)
                                or production_current.budget_reservation_id
                                != str(reservation.reservation_id)
                                or production_current.job_id != str(run.job_id)
                                or production_current.approval_binding != binding
                            ):
                                authorized = False
                                denial_reason = "production_publication_binding_mismatch"
                            else:
                                production_attempt_id = attempt.id
                                publication_binding = PublicationEffectBinding(
                                    publication_request_id=UUID(request.id),
                                    job_id=UUID(str(run.job_id)),
                                    workflow_id=info.workflow_id,
                                    workflow_run_id=info.workflow_run_id,
                                    publication_plan_id=UUID(plan_id),
                                    publication_attempt_id=UUID(attempt.id),
                                    external_effect_id=UUID(str(effect.effect_id)),
                                )
                                try:
                                    permit_request = self._production_permit_request(
                                        production_current, request, publication_binding
                                    )
                                    permit = await asyncio.to_thread(
                                        dispatch.issue_permit, permit_request
                                    )
                                except ProductionAuthorizationDenied as error:
                                    authorized = False
                                    denial_reason = error.reason_code
                                except (KeyError, TypeError, ValueError, PermissionError):
                                    authorized = False
                                    denial_reason = "production_permit_request_invalid"
                                else:
                                    production_permit_payload = {
                                        "production_operation_id": str(permit.operation_id),
                                        "production_permit_id": str(permit.permit_id),
                                    }
        await self._checkpoint("publication.authorization.checked")
        return {
            **payload,
            "authorized": authorized,
            "denial_reason": denial_reason,
            "publication_plan_id": plan_id,
            "external_effect_id": effect.effect_id,
            "budget_reservation_id": reservation.reservation_id,
            **({"publication_attempt_id": production_attempt_id} if production_attempt_id else {}),
            **production_permit_payload,
        }

    @activity.defn(name="salience.publication.delivery")
    async def delivery(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = PublicationRequest.model_validate(payload["request"])
        current, denial_reason = await self._production_action_current(payload, "delivery")
        if denial_reason is not None:
            return {
                **payload,
                "authorization_revoked": True,
                "denial_reason": denial_reason,
            }
        action_payload = (
            self._canonical_publication_payload(payload, current)
            if current is not None
            else payload
        )
        if current is not None:
            request = current.request
        capability = self._state.delivery.create(
            asset_id=current.request.ready_package_id if current is not None else request.ready_package_id,
            expires_in=timedelta(minutes=5),
        )
        await self._adapter_for(request).prepare_delivery(
            current.request if current is not None else request,
            capability.delivery_url,
        )
        await self._checkpoint("publication.delivery.prepared")
        return action_payload

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def submit_or_reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        request = PublicationRequest.model_validate(payload["request"])
        fixture_adapter = self._is_fixture_adapter(request)
        effect_key = f"{request.idempotency_key}:publish"
        current: CurrentPublicationAuthorization | None = None
        if fixture_adapter:
            attempt = await self._state.repository.create_attempt(
                publication_plan_id=payload["publication_plan_id"],
                attempt_number=1,
                idempotency_key=f"{request.idempotency_key}:attempt:1",
            )
            submitting = await self._state.store.begin_effect_submission(run, effect_key)
        else:
            existing_effect = await self._state.store.effect(run, effect_key)
            effect_may_have_started = (
                existing_effect is not None
                and existing_effect.status not in {"planned", "not_submitted"}
            )
            if effect_may_have_started:
                current, target_denial = await self._production_action_current(
                    payload, "reconcile"
                )
            else:
                current, target_denial = await self._production_target_before_submit(
                    payload, request
                )
            if target_denial is not None or current is None:
                if effect_may_have_started:
                    raise ApplicationError(
                        "publication outcome requires reconciliation under current authority",
                        type="ProductionPublicationLiabilityHeld",
                        non_retryable=True,
                    )
                return {
                    **payload,
                    "authorization_revoked": True,
                    "denial_reason": target_denial or "canonical_production_authority_invalid",
                }
            request = current.request
            attempt = SimpleNamespace(id=current.publication_attempt_id)
            payload = self._canonical_publication_payload(payload, current)
            if effect_may_have_started:
                submitting = False
            else:
                denial_reason = await self._claim_production_permit(payload, request, current)
                if denial_reason is not None:
                    await self._hold_publication_liability_if_claimed(
                        payload, "submit", denial_reason
                    )
                    await self._checkpoint("publication.authorization.revoked_before_submit")
                    return {
                        **payload,
                        "authorization_revoked": True,
                        "denial_reason": denial_reason,
                    }
                # The permit claim serializes submit authority. Only its owner may win
                # this canonical planned-to-submitting transition and resolve credentials.
                submitting = await self._state.store.begin_effect_submission(run, effect_key)
        adapter = self._adapter_for(request)
        if submitting:
            denial_reason = (
                await self._current_authorization_denial(request)
                if fixture_adapter
                else None
            )
            if denial_reason is not None:
                await self._checkpoint("publication.authorization.revoked_before_submit")
                return {
                    **payload,
                    "authorization_revoked": True,
                    "denial_reason": denial_reason,
                }
            lease = self._lease_for(request)
            receipt = await adapter.submit(request, lease)
            reconciled = False
        else:
            if not fixture_adapter:
                current, denial_reason = await self._production_action_current(
                    payload, "reconcile"
                )
                if denial_reason is not None:
                    raise ApplicationError(
                        "publication outcome requires reconciliation under current authority",
                        type="ProductionPublicationLiabilityHeld",
                        non_retryable=True,
                    )
                if current is None:
                    raise RuntimeError("canonical production publication target disappeared")
                payload = self._canonical_publication_payload(payload, current)
            receipt = await adapter.reconcile(request.idempotency_key)
            if receipt is None:
                if not fixture_adapter:
                    await self._hold_publication_liability_if_claimed(
                        payload, "reconcile", "reconciliation_unresolved"
                    )
                    raise ApplicationError(
                        "publication provider outcome remains unresolved",
                        type="ProductionPublicationLiabilityHeld",
                        non_retryable=True,
                    )
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
        current, denial_reason = await self._production_action_current(payload, "await")
        if denial_reason is not None:
            return {
                **payload,
                "authorization_revoked": True,
                "denial_reason": denial_reason,
            }
        action_payload = (
            self._canonical_publication_payload(payload, current)
            if current is not None
            else payload
        )
        if current is not None:
            request = current.request
        adapter = self._adapter_for(request)
        target_remote_id = action_payload["remote_id"]
        target_attempt_id = action_payload["publication_attempt_id"]
        target_receipt_id = action_payload["remote_receipt_id"]
        remote = await adapter.status(target_remote_id)
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
                publication_attempt_id=target_attempt_id,
                state=verified.state,
                source="webhook",
                safe_payload_hash=verified.safe_payload_hash,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
            await self._state.repository.record_verified_webhook(
                publication_attempt_id=target_attempt_id,
                status_event_id=status_event.id,
                publisher_id=verified.publisher_id,
                delivery_identity=verified.delivery_identity,
                safe_payload_hash=verified.safe_payload_hash,
                state=verified.state,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
            await self._state.repository.record_verified_webhook(
                publication_attempt_id=target_attempt_id,
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
                publication_attempt_id=target_attempt_id,
                state=state,
                source="poll",
                safe_payload_hash=_safe_hash({"remote_id": remote.remote_id, "state": state}),
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
        if state == "published":
            if current is None:
                if self._state.cost_repository is None:
                    raise RuntimeError("durable cost repository is required for publication settlement")
                settlement = await self._state.cost_repository.settle(
                    action_payload["budget_reservation_id"], actual_micros=0
                )
                if settlement.status != CostSettlementStatus.SETTLED:
                    raise RuntimeError("publication cost cannot pass completion")
            publication = await self._state.repository.record_publication(
                publication_request_id=action_payload["publication_request_id"],
                remote_receipt_id=target_receipt_id,
                state="published",
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
            await self._checkpoint("publication.published")
            return {
                **action_payload,
                "publication_state": "published",
                "publication_id": publication.id,
                "poll_count": poll_count,
            }
        return {**action_payload, "publication_state": state, "poll_count": poll_count}

    @activity.defn(name="salience.publication.complete")
    async def complete(self, payload: dict[str, Any]) -> PublicationWorkflowResult:
        run = await self._run()
        request = PublicationRequest.model_validate(payload["request"])
        if not self._is_fixture_adapter(request):
            current, denial_reason = await self._production_action_current(payload, "finalize")
            if denial_reason is not None or current is None:
                reason = denial_reason or "canonical_production_authority_invalid"
                await self._hold_publication_liability_if_claimed(
                    payload, "finalize", reason
                )
                raise ApplicationError(
                    "production publication cannot be finalized without current authority",
                    type="ProductionPublicationLiabilityHeld",
                    non_retryable=True,
                )
            payload = self._canonical_publication_payload(payload, current)
            if self._state.cost_repository is None:
                raise RuntimeError("durable cost repository is required for publication settlement")
            settlement = await self._state.cost_repository.settle(
                payload["budget_reservation_id"], actual_micros=0
            )
            if settlement.status != CostSettlementStatus.SETTLED:
                raise RuntimeError("publication cost cannot pass completion")
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
        if isinstance(payload.get("budget_reservation_id"), str) and self._state.cost_repository is not None:
            await self._state.cost_repository.release_unused(payload["budget_reservation_id"])
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
        if payload is not None:
            request = PublicationRequest.model_validate(payload["request"])
            fixture_adapter = self._is_fixture_adapter(request)
            current = None
            if not fixture_adapter:
                dispatch = self._state.production_dispatch
                local_cancel = (
                    getattr(dispatch, "cancel_unsubmitted_publication", None)
                    if dispatch is not None
                    else None
                )
                has_provider_target = any(
                    payload.get(name) is not None
                    for name in ("remote_receipt_id", "remote_id", "publication_id")
                )
                if callable(local_cancel) and not has_provider_target:
                    try:
                        reservation_id = await asyncio.to_thread(
                            local_cancel,
                            payload.get("production_operation_id"),
                            payload.get("production_permit_id"),
                            payload.get("publication_request_id"),
                            payload.get("publication_plan_id"),
                            payload.get("publication_attempt_id"),
                            payload.get("external_effect_id"),
                        )
                    except (TypeError, ValueError, ProductionAuthorizationDenied):
                        reservation_id = None
                    if reservation_id is not None:
                        if self._state.cost_repository is None:
                            raise RuntimeError(
                                "durable cost repository is required for publication cancellation"
                            )
                        await self._state.cost_repository.release_unused(str(reservation_id))
                        await self._checkpoint("publication.cancelled")
                        await self._state.store.terminal(run, "cancelled")
                        return PublicationWorkflowResult(
                            publication_state="cancelled",
                            job_id=str(run.job_id),
                            trace_id=run.trace_context.trace_id,
                            publication_request_id=payload.get("publication_request_id"),
                        )
                current, denial_reason = await self._production_action_current(payload, "cancel")
                if denial_reason is not None:
                    return PublicationWorkflowResult(
                        publication_state="ambiguous_requires_reconciliation",
                        job_id=str(run.job_id),
                        trace_id=run.trace_context.trace_id,
                        publication_request_id=payload.get("publication_request_id"),
                        denial_reason=denial_reason,
                    )
                if current is None:
                    raise RuntimeError("canonical production cancellation target disappeared")
                request = current.request
                payload = self._canonical_publication_payload(payload, current)
            if (
                (not fixture_adapter and current is not None)
                or isinstance(payload.get("remote_id"), str)
            ):
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
        if payload.get("authorization_revoked"):
            return await _execute_terminal("salience.publication.denied", payload)
        if self._cancellation_requested:
            return await _execute_terminal("salience.publication.cancel", payload)
        payload = await _execute_stage("salience.publication.submit_or_reconcile", payload)
        if payload.get("authorization_revoked"):
            return await _execute_terminal("salience.publication.denied", payload)
        while payload["publication_state"] in {"accepted", "processing"}:
            if self._cancellation_requested:
                return await _execute_terminal("salience.publication.cancel", payload)
            await workflow.sleep(timedelta(seconds=PUBLICATION_POLL_INTERVAL_SECONDS))
            payload = await _execute_stage("salience.publication.await", payload)
            if payload.get("authorization_revoked"):
                return await _execute_terminal("salience.publication.cancel", payload)
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
    if request.scheduled_publication_schedule_id is not None:
        return {
            "scheduled_publication_schedule_id": request.scheduled_publication_schedule_id,
            "contract_version": request.contract_version,
        }
    return {
        "workspace_id": request.workspace_id,
        "content_program_id": request.content_program_id,
        "ready_package_id": request.ready_package_id,
        "publisher_account_id": request.publisher_account_id,
        "publication_approval_request_id": request.publication_approval_request_id,
        "budget_id": request.budget_id,
        "idempotency_key": request.idempotency_key,
        "platform": request.platform,
        "destination": request.destination,
        "locale": request.locale,
        "territory": request.territory,
        "visibility": request.visibility,
        "capability_profile_version": request.capability_profile_version,
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
        workflow_request.publication_approval_request_id,
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
        request.publication_approval_request_id,
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
