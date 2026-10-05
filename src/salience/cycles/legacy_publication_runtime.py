"""Actual native publication with internal durable synthetic publisher only."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any

from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.exceptions import ApplicationError

from salience.cycles.authority import current_authority
from salience.cycles.legacy_publication import native_publication_request, publication_decision
from salience.publication.contracts import CredentialLease, RemotePublicationReceipt, PublicationRequest
from salience.publication.providers import FixturePublisherAdapter
from salience.publication.repository import PublicationRepository
from salience.workflows.persistence import CanonicalJobStore
from salience.workflows.publication import (
    PublicationActivities,
    PublicationWorkflowRequest,
    _workflow_request_payload,
)


async def _expired_permit_receipt_recovery(outbox, binding, request=None):
    """Validate the sole permitted expired-claim path: exact receipt readback.

    This can run only inside the original submit/reconcile activity and only
    after an exact immutable fixture receipt plus a submitting/completed
    canonical effect are already durable. It never renews the execution permit
    or makes status progress.
    """
    from salience.cycles.legacy_runtime import require_expired_publication_permit

    info = activity.info()
    if info.activity_type != "salience.publication.submit_or_reconcile":
        raise PermissionError("expired publication claim permits receipt readback only")
    if not await asyncio.to_thread(require_expired_publication_permit, outbox, binding):
        return False

    expected = native_publication_request(binding)
    run = await CanonicalJobStore(outbox.database_url).run_for_workflow(
        info.workflow_id, info.workflow_run_id
    )
    store = CanonicalJobStore(outbox.database_url)
    effect = await store.effect(run, expected.idempotency_key + ":publish")
    if effect is None or effect.status not in {"submitting", "completed"}:
        raise PermissionError("expired publication permit has no submitted canonical effect")

    def read_receipt():
        with outbox._connect() as connection:
            return connection.execute(
                """SELECT receipt.*,progress.state AS progress_state,progress.polls,
                          encode(digest(receipt.request::jsonb::text,'sha256'),'hex') AS actual_fingerprint
                   FROM v4_legacy_publication_fixture_receipts receipt
                   JOIN v4_legacy_publication_fixture_progress progress USING(operation_id)
                   WHERE receipt.operation_id=%s AND receipt.workspace_id=%s""",
                (binding["operation_id"], outbox.workspace_id),
            ).fetchone()

    row = await asyncio.to_thread(read_receipt)
    if not row:
        raise PermissionError("expired publication permit has no durable receipt to reconcile")
    if (
        row["idempotency_key"] != expected.idempotency_key
        or row["remote_id"] != "fixture-v4-publication:" + str(binding["operation_id"])
        or row["fingerprint"] != row["actual_fingerprint"]
        or row["request"].get("idempotency_key") != expected.idempotency_key
        or row["request"].get("workspace_id") != expected.workspace_id
        or row["request"].get("content_program_id") != expected.content_program_id
        or row["request"].get("ready_package_id") != expected.ready_package_id
        or row["request"].get("publisher_account_id") != expected.publisher_account_id
        or row["request"].get("publication_approval_request_id")
        != expected.publication_approval_request_id
    ):
        raise PermissionError("exact original immutable publication receipt required")

    stored_receipt = RemotePublicationReceipt.model_validate(row["receipt"])
    if (
        stored_receipt.id != "fixture-v4-receipt:" + str(binding["operation_id"])
        or stored_receipt.publication_attempt_id != row["request"].get("id")
        or stored_receipt.publisher_id != "fixture-publisher"
        or stored_receipt.remote_id != row["remote_id"]
        or stored_receipt.state != "accepted"
    ):
        raise PermissionError("original accepted publication receipt required")
    if effect.status == "completed" and effect.external_id != row["remote_id"]:
        raise PermissionError("canonical publication effect and durable receipt conflict")

    repository = PublicationRepository(outbox.database_url)
    current = await repository.load_current_authorization(row["request"]["id"])
    if current.request.model_dump(mode="json") != row["request"]:
        raise PermissionError("current persisted publication request must match original receipt")
    if request is not None and current.request != request:
        raise PermissionError("original publication request required for receipt readback")
    if not publication_decision(current, budget_status="reserved").allowed:
        raise PermissionError("current native publication authorization required for receipt readback")

    from salience.cycles.legacy_dispatch import LegacyDispatch
    from salience.cycles.legacy_publication import require_publication_reconciliation

    bridge = LegacyDispatch(
        outbox.database_url,
        workspace_id=outbox.workspace_id,
        subject_id=binding["actor_id"],
        task_queue=binding["task_queue"],
    )
    await asyncio.to_thread(require_publication_reconciliation, bridge, binding)
    return True


class DurableLegacyFixturePublisher(FixturePublisherAdapter):
    """A controlled synthetic remote with durable original-operation receipts.

    Constructed only by the compatibility worker. No adapter/credential/media
    transport injection is accepted. Native business effects remain synthetic.
    """
    def __init__(self,outbox):
        super().__init__()
        self.outbox=outbox

    @property
    def submit_count(self):
        # Native result reflects this worker's simulation submissions; durable
        # receipt count is the independent proof across worker processes.
        return self._submit_count

    async def _binding(self,request=None,*,reconcile=False,cancel=False):
        from salience.cycles.legacy_runtime import binding_for_job,require_current_stage
        info=activity.info()
        run=await CanonicalJobStore(self.outbox.database_url).run_for_workflow(info.workflow_id)
        binding=await asyncio.to_thread(binding_for_job,self.outbox,run.job_id)
        if binding.get('stage')!='publication':raise PermissionError('original canonical publication stage required')
        execution_permit_current = True
        if cancel:
            if info.activity_type!='salience.publication.cancel':raise PermissionError('original ordered publication cancellation required')
        else:
            try:
                await asyncio.to_thread(require_current_stage,self.outbox,binding)
            except Exception:
                if not reconcile:
                    raise
                await _expired_permit_receipt_recovery(self.outbox, binding, request)
                execution_permit_current = False
        if reconcile:
            from salience.cycles.legacy_dispatch import LegacyDispatch
            from salience.cycles.legacy_publication import require_publication_reconciliation
            bridge=LegacyDispatch(self.outbox.database_url,workspace_id=self.outbox.workspace_id,subject_id=binding['actor_id'],task_queue=binding['task_queue'])
            await asyncio.to_thread(require_publication_reconciliation,bridge,binding)
        if request is not None:
            current=await PublicationRepository(self.outbox.database_url).load_current_authorization(request.id)
            expected=native_publication_request(binding)
            if current.request!=request or (request.workspace_id,request.content_program_id,request.ready_package_id,request.publisher_account_id,request.idempotency_key)!=(expected.workspace_id,expected.content_program_id,expected.ready_package_id,expected.publisher_account_id,expected.idempotency_key):
                raise PermissionError('original current synthetic publication request required')
            decision=publication_decision(current,budget_status='reserved')
            if not decision.allowed:raise PermissionError('current native synthetic publication authorization required')
        binding["_execution_permit_current"] = execution_permit_current
        return binding

    async def prepare_delivery(self,request,delivery_url):
        await self._binding(request)
        if not delivery_url.startswith('https://'):
            raise PermissionError('native fixture delivery capability required')
        # The native fixture never fetches a delivery URL or publication bytes.
        return None

    async def submit(self,request,lease):
        binding=await self._binding(request)
        if (
            type(lease) is not CredentialLease
            or lease.reveal() != "fixture-publication-lease"
            or lease.expires_at <= datetime.now(UTC)
            or lease.granted_scopes != frozenset({"publish:create"})
        ):
            raise PermissionError('internal fixture credential lease required')
        def commit():
            with self.outbox._connect() as connection:
                connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('fixture-publication:'+str(binding['operation_id']),))
                encoded=request.model_dump(mode='json')
                fingerprint=connection.execute("SELECT encode(digest(%s::jsonb::text,'sha256'),'hex') AS fingerprint",(Jsonb(encoded),)).fetchone()['fingerprint']
                prior=connection.execute('SELECT * FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s',(binding['operation_id'],)).fetchone()
                if prior:
                    if prior['fingerprint']!=fingerprint or prior['idempotency_key']!=request.idempotency_key:raise PermissionError('original synthetic publication fingerprint conflict')
                    return RemotePublicationReceipt.model_validate(prior['receipt'])
                remote='fixture-v4-publication:'+str(binding['operation_id'])
                receipt=RemotePublicationReceipt(id='fixture-v4-receipt:'+str(binding['operation_id']),publication_attempt_id=request.id,publisher_id='fixture-publisher',remote_id=remote,state='accepted',safe_metadata_hash=sha256(json.dumps(encoded,sort_keys=True).encode()).hexdigest(),remote_url='https://fixture.invalid/publications/'+str(binding['operation_id']))
                connection.execute('INSERT INTO v4_legacy_publication_fixture_receipts(operation_id,workspace_id,idempotency_key,fingerprint,remote_id,request,receipt) VALUES(%s,%s,%s,%s,%s,%s,%s)',(binding['operation_id'],self.outbox.workspace_id,request.idempotency_key,fingerprint,remote,Jsonb(encoded),Jsonb(receipt.model_dump(mode='json'))))
                connection.execute("INSERT INTO v4_legacy_publication_fixture_progress(operation_id,state) VALUES(%s,'accepted')",(binding['operation_id'],))
                self._submit_count+=1
                return receipt
        return await asyncio.to_thread(commit)

    async def reconcile(self,idempotency_key):
        binding=await self._binding(reconcile=True)
        if idempotency_key!='v4-legacy:'+str(binding['operation_id']):raise PermissionError('original synthetic reconciliation key required')
        return await asyncio.to_thread(self._receipt,binding,None)

    def _receipt(self,binding,transition):
        with self.outbox._connect() as connection:
            row=connection.execute('SELECT receipt.*,progress.state,progress.polls FROM v4_legacy_publication_fixture_receipts receipt JOIN v4_legacy_publication_fixture_progress progress USING(operation_id) WHERE receipt.operation_id=%s AND receipt.workspace_id=%s FOR UPDATE OF progress',(binding['operation_id'],self.outbox.workspace_id)).fetchone()
            if not row:return None
            state=row['state'];polls=row['polls']
            if transition=='poll' and state in {'accepted','processing'}:
                polls+=1;state='processing' if polls==1 else 'published'
            if transition=='cancel' and state in {'accepted','processing'}:state='cancelled'
            if (state,polls)!=(row['state'],row['polls']):
                connection.execute('UPDATE v4_legacy_publication_fixture_progress SET state=%s,polls=%s,updated_at=clock_timestamp() WHERE operation_id=%s',(state,polls,binding['operation_id']))
            return RemotePublicationReceipt.model_validate(row['receipt']).model_copy(update={'state':state})

    async def status(self,remote_id):
        binding=await self._binding(reconcile=True)
        if remote_id!='fixture-v4-publication:'+str(binding['operation_id']):raise PermissionError('original synthetic remote receipt required')
        transition = 'poll' if binding.get("_execution_permit_current") else None
        return await asyncio.to_thread(self._receipt,binding,transition)

    async def cancel(self,remote_id):
        binding=await self._binding(cancel=True)
        if remote_id!='fixture-v4-publication:'+str(binding['operation_id']):raise PermissionError('original synthetic cancellation receipt required')
        return await asyncio.to_thread(self._receipt,binding,'cancel')


class GuardedPublicationActivities(PublicationActivities):
    def __init__(self,state,outbox):
        super().__init__(state);self.outbox=outbox

    async def _run(self):
        from salience.cycles.legacy_runtime import binding_for_job,require_current_stage
        run=await super()._run()
        binding=await asyncio.to_thread(binding_for_job,self.outbox,run.job_id)
        if binding.get('stage')!='publication':raise PermissionError('original canonical publication stage required')
        if activity.info().activity_type!='salience.publication.cancel':
            try:
                await asyncio.to_thread(require_current_stage,self.outbox,binding)
            except Exception:
                if activity.info().activity_type != "salience.publication.submit_or_reconcile":
                    raise
                await _expired_permit_receipt_recovery(self.outbox,binding)
        return run

    async def _guard(self,payload):
        from salience.cycles.legacy_runtime import binding_for_job
        run=await self._run();binding=await asyncio.to_thread(binding_for_job,self.outbox,run.job_id)
        expected=native_publication_request(binding)
        if payload['workflow_request']!=_workflow_request_payload(expected):raise PermissionError('original fixed fixture publication payload required')
        current=await self._state.repository.load_current_authorization(payload['publication_request_id'])
        if current.request.model_dump(mode='json')!=payload['request']:raise PermissionError('original current publication projection required')
        if activity.info().activity_type!='salience.publication.cancel':
            decision=publication_decision(current,budget_status='reserved')
            if not decision.allowed:raise PermissionError('current native publication authorization required')

    @activity.defn(name='salience.publication.request')
    async def request(self,request:PublicationWorkflowRequest)->dict[str,Any]:
        from salience.cycles.legacy_runtime import binding_for_job
        run=await self._run();binding=await asyncio.to_thread(binding_for_job,self.outbox,run.job_id)
        if request!=native_publication_request(binding):raise PermissionError('original fixed fixture publication payload required')
        return await super().request(request)

    @activity.defn(name="salience.publication.authorize")
    async def authorize(self,payload:dict[str,Any]):
        await self._guard(payload)
        return await super().authorize(payload)

    @activity.defn(name="salience.publication.delivery")
    async def delivery(self,payload:dict[str,Any]):
        await self._guard(payload)
        return await super().delivery(payload)

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def submit_or_reconcile(self,payload:dict[str,Any]):
        await self._guard(payload)
        run = await self._run()
        binding = await asyncio.to_thread(
            __import__("salience.cycles.legacy_runtime", fromlist=["binding_for_job"]).binding_for_job,
            self.outbox,
            run.job_id,
        )
        recovery = await _expired_permit_receipt_recovery(self.outbox, binding)
        result = await super().submit_or_reconcile(payload)
        if recovery:
            await self._checkpoint("publication.receipt_recovered_requires_typed_recovery")
            raise ApplicationError(
                "durable publication receipt recovered; typed execution recovery is required",
                type="LegacyPublicationRecoveryHeld",
                non_retryable=True,
            )
        return result

    @activity.defn(name="salience.publication.await")
    async def await_publication(self,payload:dict[str,Any]):
        await self._guard(payload)
        return await super().await_publication(payload)

    @activity.defn(name="salience.publication.complete")
    async def complete(self,payload:dict[str,Any]):
        await self._guard(payload)
        return await super().complete(payload)

    @activity.defn(name="salience.publication.denied")
    async def denied(self,payload:dict[str,Any]):
        await self._guard(payload)
        return await super().denied(payload)

    @activity.defn(name="salience.publication.cancel")
    async def cancel(self,payload:dict[str,Any]):
        if payload is None:
            return await super().cancel(payload)

        await self._guard(payload)
        remote_id = payload.get("remote_id")
        if not isinstance(remote_id, str):
            return await super().cancel(payload)

        request = PublicationRequest.model_validate(payload["request"])
        receipt = await self._adapter_for(request).cancel(remote_id)
        if receipt is None:
            raise RuntimeError("durable publication cancellation readback is pending")
        if receipt.remote_id != remote_id:
            raise PermissionError("original publication cancellation receipt required")

        run = await self._run()
        if receipt.state == "published":
            raise ApplicationError(
                "published publication readback during cancellation requires typed execution recovery",
                type="LegacyPublicationRecoveryHeld",
                non_retryable=True,
            )
        if receipt.state != "cancelled":
            raise RuntimeError("remote publication cancellation is not confirmed")

        attempt_id = payload.get("publication_attempt_id")
        if isinstance(attempt_id, str):
            await self._state.repository.record_status_event(
                publication_attempt_id=attempt_id,
                state=receipt.state,
                source="cancellation",
                safe_payload_hash=receipt.safe_metadata_hash,
                trace_id=run.trace_context.trace_id,
                span_id=run.trace_context.span_id,
            )
        # The receipt is already durably cancelled. Remove the remote id so the
        # native base activity only performs local budget/job terminal work.
        return await super().cancel({key: value for key, value in payload.items() if key != "remote_id"})

    @activity.defn(name="salience.publication.dead_letter")
    async def dead_letter(self,payload:dict[str,Any]):
        await self._guard(payload)
        return await super().dead_letter(payload)
