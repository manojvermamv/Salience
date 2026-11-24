"""Same-run publication recovery and replay on a real Temporal server."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from salience.workflows.publication import (
    GovernedPublicationWorkflow,
    PublicationWorkflowRequest,
    PublicationWorkflowResult,
    _execute_stage,
    _execute_terminal,
)


_REQUEST = PublicationWorkflowRequest(
    workspace_id="workspace", content_program_id="program", ready_package_id="ready",
    publisher_account_id="account", publication_approval_request_id="approval",
    budget_id="budget", idempotency_key="original-provider-key",
)


class _PublicationStages:
    """Control committed activity outcomes while Temporal runs the workflow itself."""

    def __init__(self, *, expires_at_stage, receipt_outcome, seconds=5):
        self.expires_at_stage = expires_at_stage
        self.receipt_outcome = receipt_outcome
        self.seconds = seconds
        self.renewal_checked = asyncio.Event()
        self.allow_renewal_reply = asyncio.Event()
        self.allow_renewal_reply.set()
        self.runs = set()
        self.renewals = []
        self.delivery_permits = []
        self.submit_permits = []
        self.cancel_permits = []

    def _remember_run(self):
        info = activity.info()
        self.runs.add((info.workflow_id, info.workflow_run_id))

    @activity.defn(name="salience.publication.request")
    async def request(self, request: PublicationWorkflowRequest) -> dict:
        self._remember_run()
        return {"idempotency_key": request.idempotency_key, "publication_attempt_id": "original-attempt"}

    @activity.defn(name="salience.publication.authorize")
    async def authorize(self, payload: dict) -> dict:
        self._remember_run()
        if payload.get("preclaim_resume") is True:
            self.renewals.append(payload)
            self.renewal_checked.set()
            await self.allow_renewal_reply.wait()
            if self.receipt_outcome == "pending":
                return {**payload, "authorized": False, "preclaim_resume_pending": True}
            if self.receipt_outcome == "denied":
                return {**payload, "authorized": False, "denial_reason": "approval_revoked"}
            payload = {key: value for key, value in payload.items() if key != "authorization_revoked"}
            return {**payload, "authorized": True, "production_permit_id": "successor"}
        return {
            **payload, "authorized": True, "production_permit_id": "predecessor",
            "production_preclaim_resume_until": (datetime.now(UTC) + timedelta(seconds=self.seconds)).isoformat(),
        }

    def _expire(self, payload):
        return {
            **payload, "authorization_revoked": True, "denial_reason": "permit_expired",
            "preclaim_permit_expired": True,
        }

    @activity.defn(name="salience.publication.delivery")
    async def delivery(self, payload: dict) -> dict:
        self._remember_run()
        permit = payload["production_permit_id"]
        self.delivery_permits.append(permit)
        if self.expires_at_stage == "delivery" and permit == "predecessor":
            return self._expire(payload)
        if self.expires_at_stage == "both":
            return self._expire(payload)
        return payload

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def submit(self, payload: dict) -> dict:
        self._remember_run()
        permit = payload["production_permit_id"]
        if self.expires_at_stage == "claim" and permit == "predecessor":
            return self._expire(payload)
        assert payload["publication_attempt_id"] == "original-attempt"
        assert payload["idempotency_key"] == "original-provider-key"
        self.submit_permits.append(permit)
        return {**payload, "publication_state": "published"}

    @activity.defn(name="salience.publication.complete")
    async def complete(self, payload: dict) -> PublicationWorkflowResult:
        return PublicationWorkflowResult("published", "original-job", "trace")

    @activity.defn(name="salience.publication.denied")
    async def denied(self, payload: dict) -> PublicationWorkflowResult:
        return PublicationWorkflowResult(
            "denied", "original-job", "trace", denial_reason=payload.get("denial_reason"),
        )

    @activity.defn(name="salience.publication.cancel")
    async def cancel(self, payload: dict) -> PublicationWorkflowResult:
        self.cancel_permits.append(payload["production_permit_id"])
        return PublicationWorkflowResult("cancelled", "original-job", "trace")

    @activity.defn(name="salience.publication.dead_letter")
    async def dead_letter(self, payload: dict) -> PublicationWorkflowResult:
        return PublicationWorkflowResult(
            "ambiguous_requires_reconciliation", "original-job", "trace",
            denial_reason=payload.get("failure_reason"),
        )

    @property
    def activities(self):
        return [self.request, self.authorize, self.delivery, self.submit,
                self.complete, self.denied, self.cancel, self.dead_letter]


@workflow.defn(name="GovernedPublicationWorkflow")
class _HistoricalPublicationWorkflow:
    """The pre-renewal history of an execution denied during delivery."""

    @workflow.run
    async def run(self, request: PublicationWorkflowRequest) -> PublicationWorkflowResult:
        payload = await _execute_stage("salience.publication.request", request)
        payload = await _execute_stage("salience.publication.authorize", payload)
        payload = await _execute_stage("salience.publication.delivery", payload)
        return await _execute_terminal("salience.publication.denied", payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at_stage", ["delivery", "claim"])
async def test_preclaim_resume_submits_one_successor_in_the_original_temporal_run(expires_at_stage):
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = f"preclaim-{uuid4()}"
    stages = _PublicationStages(expires_at_stage=expires_at_stage, receipt_outcome="allow")
    async with Worker(client, task_queue=queue, workflows=[GovernedPublicationWorkflow], activities=stages.activities):
        handle = await client.start_workflow(GovernedPublicationWorkflow.run, _REQUEST, id=queue, task_queue=queue)
        result = await asyncio.wait_for(handle.result(), 10)

    assert result.publication_state == "published"
    assert stages.runs == {(handle.id, handle.first_execution_run_id)}
    assert [payload["production_permit_id"] for payload in stages.renewals] == ["predecessor"]
    assert stages.delivery_permits == ["predecessor", "successor"]
    assert stages.submit_permits == ["successor"]
    await Replayer(workflows=[GovernedPublicationWorkflow]).replay_workflow(await handle.fetch_history())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("receipt_outcome", "expires_at_stage", "state", "reason"),
    [("pending", "delivery", "denied", "permit_expired"),
     ("stalled", "delivery", "ambiguous_requires_reconciliation", "preclaim_resume_outcome_unknown"),
     ("denied", "delivery", "denied", "approval_revoked"),
     ("allow", "both", "denied", "permit_expired")],
)
async def test_temporal_never_submits_without_an_authorized_live_successor(
    receipt_outcome, expires_at_stage, state, reason,
):
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = f"preclaim-{uuid4()}"
    stages = _PublicationStages(expires_at_stage=expires_at_stage, receipt_outcome=receipt_outcome, seconds=2)
    if receipt_outcome == "stalled":
        stages.allow_renewal_reply.clear()
    async with Worker(client, task_queue=queue, workflows=[GovernedPublicationWorkflow], activities=stages.activities):
        handle = await client.start_workflow(GovernedPublicationWorkflow.run, _REQUEST, id=queue, task_queue=queue)
        try:
            if receipt_outcome == "stalled":
                await asyncio.wait_for(stages.renewal_checked.wait(), 3)
                await handle.signal(GovernedPublicationWorkflow.request_cancellation)
            result = await asyncio.wait_for(handle.result(), 10)
        finally:
            stages.allow_renewal_reply.set()

    assert (result.publication_state, result.denial_reason) == (state, reason)
    assert stages.renewals
    if receipt_outcome not in {"pending", "stalled"}:
        assert len(stages.renewals) == 1
    assert stages.submit_permits == []
    assert stages.cancel_permits == []


@pytest.mark.asyncio
async def test_cancellation_during_renewal_uses_new_permit_for_local_cancel_and_skips_submit():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = f"preclaim-{uuid4()}"
    stages = _PublicationStages(expires_at_stage="delivery", receipt_outcome="allow")
    stages.allow_renewal_reply.clear()
    async with Worker(client, task_queue=queue, workflows=[GovernedPublicationWorkflow], activities=stages.activities):
        handle = await client.start_workflow(GovernedPublicationWorkflow.run, _REQUEST, id=queue, task_queue=queue)
        await asyncio.wait_for(stages.renewal_checked.wait(), 3)
        await handle.signal(GovernedPublicationWorkflow.request_cancellation)
        stages.allow_renewal_reply.set()
        result = await asyncio.wait_for(handle.result(), 10)

    assert result.publication_state == "cancelled"
    assert stages.cancel_permits == ["successor"]
    assert stages.delivery_permits == ["predecessor"]
    assert stages.submit_permits == []


@pytest.mark.asyncio
async def test_historical_terminal_expiry_replays_without_scheduling_renewal():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = f"preclaim-legacy-{uuid4()}"
    stages = _PublicationStages(expires_at_stage="delivery", receipt_outcome="allow")
    async with Worker(client, task_queue=queue, workflows=[_HistoricalPublicationWorkflow], activities=stages.activities):
        handle = await client.start_workflow(_HistoricalPublicationWorkflow.run, _REQUEST, id=queue, task_queue=queue)
        assert (await asyncio.wait_for(handle.result(), 10)).publication_state == "denied"

    await Replayer(workflows=[GovernedPublicationWorkflow]).replay_workflow(await handle.fetch_history())
    assert stages.renewals == []
    assert stages.submit_permits == []
