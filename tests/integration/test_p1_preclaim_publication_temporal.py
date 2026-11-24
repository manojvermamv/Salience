"""Drive a same-run renewal through Temporal, PostgreSQL, and a recording publisher."""

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from salience.governance.cost_repository import CostReservationRepository
from salience.publication.repository import PublicationRepository, _fingerprint
from salience.workflows.persistence import CanonicalJobStore
from salience.workflows.publication import (
    GovernedPublicationWorkflow,
    PublicationActivities,
    PublicationWorkflowRequest,
    PublicationWorkflowState,
    _fixture_lease,
    _workflow_request_payload,
)
from test_p1_preclaim_resume_dispatch import _operator_resume
from test_p1_production_policy_revocation import accounting, cycles, policy
from test_p1_production_recovery_case_opening import opening_database, seed_opening_source


@pytest.fixture
def temporal_publication_source(opening_database, monkeypatch, request):
    monkeypatch.setenv("TEST_DATABASE_URL", opening_database)
    accounting_state = request.getfixturevalue("accounting")

    def seed(workflow_id, run_id):
        case, authority, opening_fields = seed_opening_source(
            accounting_state, opening_database, "preclaim_live",
            workflow_id=workflow_id, workflow_run_id=run_id,
        )
        permit_request = case["request"]
        binding = permit_request.publication_binding
        attempt_key = f"{permit_request.idempotency_key}:attempt:1"
        with psycopg.connect(opening_database) as connection:
            # Match the fixture's prebound attempt to the ordinary
            # idempotent authorization activity.
            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                "UPDATE publication_attempts SET request_fingerprint=%s WHERE id=%s",
                (_fingerprint({"publication_plan_id": str(binding.publication_plan_id),
                               "attempt_number": 1, "idempotency_key": attempt_key}),
                 binding.publication_attempt_id),
            )
            connection.execute(
                """UPDATE publisher_connections
                   SET granted_scopes=granted_scopes || '["publish:create"]'::jsonb
                   WHERE publisher_account_id=%s""",
                (permit_request.account_id,),
            )
            connection.execute(
                """UPDATE publisher_capability_profiles
                   SET capability_facts=capability_facts || '{
                     "contract_compatibility":{"publication":"1.0"},
                     "granted_scopes":["effects:write","publish:create"]
                   }'::jsonb
                   WHERE publisher_account_id=%s AND publisher_id=%s""",
                (permit_request.account_id, permit_request.provider_id),
            )
            connection.execute("SET LOCAL session_replication_role=origin")
            asset_id = connection.execute(
                """INSERT INTO assets(workspace_id,content_program_id,storage_key,content_hash,
                                      media_type,byte_size,origin_type)
                   VALUES(%s,%s,%s,%s,'video/mp4',1,'fixture') RETURNING id""",
                (permit_request.workspace_id, permit_request.program_id,
                 f"fixture/{binding.publication_request_id}.mp4", permit_request.artifact_sha256),
            ).fetchone()[0]
            license_id = connection.execute(
                """INSERT INTO asset_licenses(asset_id,license_type,commercial_use,terms,status)
                   VALUES(%s,'fixture',true,'{"permitted_channels":["p1-local"],"territories":["US"]}',
                          'active') RETURNING id""",
                (asset_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO asset_rights_links(asset_id,link_key,asset_license_id)
                   VALUES(%s,'fixture-license',%s)""",
                (asset_id, license_id),
            )
            connection.execute(
                """INSERT INTO distribution_package_assets(distribution_package_id,asset_id,asset_role)
                   SELECT distribution_package_id,%s,'video' FROM ready_to_publish_packages WHERE id=%s""",
                (asset_id, case["publication_rows"]["ready_package_id"]),
            )
        return case, authority, opening_fields

    return seed


class _RecordingPublisher:
    def __init__(self, publisher_id):
        self.capabilities = SimpleNamespace(publisher_id=publisher_id, version="1")
        self.prepared = []
        self.submitted = []
        self.remote_id = f"local-recorded-{uuid4()}"

    async def prepare_delivery(self, request, delivery_url):
        self.prepared.append(request.idempotency_key)

    def _receipt(self, state):
        return SimpleNamespace(
            publisher_id=self.capabilities.publisher_id, remote_id=self.remote_id,
            state=state, safe_metadata_hash="c" * 64, remote_url=None,
        )

    async def submit(self, request, credential_lease):
        self.submitted.append(request.idempotency_key)
        return self._receipt("accepted")

    async def status(self, remote_id):
        assert remote_id == self.remote_id
        return self._receipt("published")

    async def reconcile(self, idempotency_key):
        raise AssertionError("a successfully acknowledged submit should not reconcile")


class _LostRenewalReply:
    def __init__(self, dispatch, *, reply_behavior):
        self.dispatch = dispatch
        self.reply_behavior = reply_behavior
        self.loop = asyncio.get_running_loop()
        self.waiting = asyncio.Event()
        self.issued_results = []

    def __getattr__(self, name):
        return getattr(self.dispatch, name)

    def issue_preclaim_resume_permit(self, request, *, predecessor_permit_id):
        result = self.dispatch.issue_preclaim_resume_permit(
            request, predecessor_permit_id=predecessor_permit_id,
        )
        if result is None:
            self.loop.call_soon_threadsafe(self.waiting.set)
        else:
            self.issued_results.append(result)
            if self.reply_behavior == "revoke" and len(self.issued_results) == 1:
                with psycopg.connect(self.dispatch.database_url) as connection:
                    connection.execute(
                        "UPDATE policy_versions SET status='revoked' WHERE id=%s",
                        (request.policy_version_id,),
                    )
            if self.reply_behavior == "drop_all" or len(self.issued_results) == 1:
                raise RuntimeError("local simulation of a reply lost after the permit transaction")
        return result


class _PreboundPublicationActivities(PublicationActivities):
    """Use a prebound production request and expire only its fixture predecessor."""

    def __init__(self, state, case, *, expiry_stage):
        super().__init__(state)
        self.case = case
        self.expiry_stage = expiry_stage

    @activity.defn(name="salience.publication.request")
    async def request(self, workflow_request):
        original = self.case["request"]
        binding = original.publication_binding
        request = await self._state.repository.load_request(str(binding.publication_request_id))
        workflow_request = PublicationWorkflowRequest(
            workspace_id=request.workspace_id, content_program_id=request.content_program_id,
            ready_package_id=request.ready_package_id, publisher_account_id=request.publisher_account_id,
            publication_approval_request_id=request.publication_approval_request_id,
            budget_id=str(original.budget_id), idempotency_key=request.idempotency_key,
            platform=request.platform, destination=request.destination, locale=request.locale,
            territory=request.territory, visibility=request.visibility,
            capability_profile_version=request.capability_profile_version,
        )
        run = await self._run()
        return {
            "workflow_request": _workflow_request_payload(workflow_request),
            "request": request.model_dump(mode="json"),
            "publication_request_id": request.id,
            "publication_plan_id": str(binding.publication_plan_id),
            "trace_id": run.trace_context.trace_id,
            "span_id": run.trace_context.span_id,
        }

    @activity.defn(name="salience.publication.delivery")
    async def delivery(self, payload: dict) -> dict:
        await self._expire_initial_permit_at(payload, "delivery")
        return await super().delivery(payload)

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def submit_or_reconcile(self, payload: dict) -> dict:
        await self._expire_initial_permit_at(payload, "claim")
        return await super().submit_or_reconcile(payload)

    async def _expire_initial_permit_at(self, payload, stage):
        if stage == self.expiry_stage and payload["production_permit_id"] == str(self.case["opening_permit_id"]):
            await asyncio.to_thread(self._expire_predecessor)

    def _expire_predecessor(self):
        with psycopg.connect(self.case["database"]) as connection:
            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                """UPDATE v4_production_effect_permits
                   SET expires_at=least(expires_at,clock_timestamp())
                   WHERE permit_id=%s""",
                (self.case["opening_permit_id"],),
            )

    @property
    def activities(self):
        return [self.request, self.authorize, self.delivery, self.submit_or_reconcile,
                self.await_publication, self.complete, self.denied, self.cancel, self.dead_letter]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expiry_stage", "reply_behavior", "expected_replies"),
    [
        pytest.param("delivery", "drop_first", 2, id="expired-before-delivery"),
        pytest.param("claim", "drop_first", 2, id="expired-before-claim"),
        pytest.param("delivery", "drop_all", 3, id="all-renewal-replies-lost"),
        pytest.param("delivery", "revoke", 1, id="policy-revoked-after-lost-reply"),
    ],
)
async def test_committed_operator_resume_survives_lost_reply_in_same_publication_run(
    temporal_publication_source, expiry_stage, reply_behavior, expected_replies,
):
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = f"preclaim-postgres-{uuid4()}"
    handle = await client.start_workflow(
        GovernedPublicationWorkflow.run,
        PublicationWorkflowRequest(scheduled_publication_schedule_id="prebound-fixture"),
        id=queue, task_queue=queue,
    )
    case, authority, opening_fields = await asyncio.to_thread(
        temporal_publication_source, handle.id, handle.first_execution_run_id,
    )
    original = case["request"]
    binding = original.publication_binding
    provider = _RecordingPublisher(original.provider_id)
    dispatch = _LostRenewalReply(case["dispatch"], reply_behavior=reply_behavior)
    state = PublicationWorkflowState(
        store=CanonicalJobStore(case["database"]), repository=PublicationRepository(case["database"]),
        cost_repository=CostReservationRepository(case["database"]),
        publisher_adapters={original.provider_id: provider},
        credential_lease_factory=lambda _request: _fixture_lease(),
        production_dispatch=dispatch,
        production_permit_request_factory=lambda _approval, _publication: original,
        preclaim_resume_delivery_profiles=frozenset({(original.provider_id, "1", 1)}),
    )
    stages = _PreboundPublicationActivities(state, case, expiry_stage=expiry_stage)

    async def wait_for_review_timer():
        while True:
            history = await handle.fetch_history()
            if any(event.HasField("timer_started_event_attributes") for event in history.events):
                return
            await asyncio.sleep(0.05)

    worker_options = dict(
        task_queue=queue, workflows=[GovernedPublicationWorkflow], activities=stages.activities,
        sticky_queue_schedule_to_start_timeout=timedelta(seconds=1),
    )
    async with Worker(client, **worker_options):
        terminal = asyncio.create_task(handle.result())
        waiting = asyncio.create_task(dispatch.waiting.wait())
        try:
            await asyncio.wait({terminal, waiting}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
            assert not terminal.done(), f"workflow terminated before waiting for review: {terminal.result()}"
            assert waiting.done(), "workflow did not wait for an exact operator resume"
            await asyncio.wait_for(wait_for_review_timer(), 5)
            assert provider.submitted == []
        finally:
            waiting.cancel()
            terminal.cancel()

    await asyncio.to_thread(_operator_resume, case, authority, opening_fields)
    async with Worker(client, **worker_options):
        try:
            result = await asyncio.wait_for(handle.result(), 20)
        except TimeoutError:
            history = await handle.fetch_history()
            pytest.fail(
                f"publication did not finish after restart. History event types: "
                f"{[event.event_type for event in history.events]}, "
                f"renewal replies: {len(dispatch.issued_results)}, submissions: {len(provider.submitted)}"
            )

    assert result.job_id == str(binding.job_id)
    assert result.publication_attempt_id == str(binding.publication_attempt_id)
    outcome_unknown = reply_behavior != "drop_first"
    if outcome_unknown:
        assert (result.publication_state, result.denial_reason) == (
            "ambiguous_requires_reconciliation", "preclaim_resume_outcome_unknown",
        )
        assert provider.prepared == provider.submitted == []
    else:
        assert result.publication_state == "published", result
        expected_preparations = 1 if expiry_stage == "delivery" else 2
        assert provider.prepared == [original.idempotency_key] * expected_preparations
        assert provider.submitted == [original.idempotency_key]
    assert len(dispatch.issued_results) == expected_replies
    first_reply = dispatch.issued_results[0]
    assert all(reply == first_reply for reply in dispatch.issued_results)
    assert first_reply.generation == 2
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            """SELECT permit_id, generation, request_snapshot->>'workflow_run_id',
                      request_snapshot->>'idempotency_key'
               FROM v4_production_effect_permits WHERE operation_id=%s ORDER BY generation""",
            (original.operation_id,),
        ).fetchall() == [
            (case["opening_permit_id"], 1, handle.first_execution_run_id, original.idempotency_key),
            (first_reply.permit_id, 2, handle.first_execution_run_id, original.idempotency_key),
        ]
        assert connection.execute(
            "SELECT permit_id FROM v4_production_effect_permit_claims WHERE operation_id=%s",
            (original.operation_id,),
        ).fetchall() == ([] if outcome_unknown else [(first_reply.permit_id,)])
        assert connection.execute(
            "SELECT id FROM publication_attempts WHERE publication_plan_id=%s",
            (binding.publication_plan_id,),
        ).fetchall() == [(binding.publication_attempt_id,)]
        assert connection.execute(
            "SELECT id,status FROM budget_reservations WHERE external_effect_id=%s",
            (binding.external_effect_id,),
        ).fetchall() == [(original.budget_reservation_id, "reserved" if outcome_unknown else "settled")]
        if outcome_unknown:
            assert connection.execute(
                """SELECT job.state, effect.status, held.error_type
                   FROM jobs job JOIN external_effects effect ON effect.job_id=job.id
                   JOIN job_dead_letters held ON held.job_id=job.id
                   WHERE job.id=%s AND effect.id=%s""",
                (binding.job_id, binding.external_effect_id),
            ).fetchall() == [("dead_lettered", "planned", "preclaim_resume_outcome_unknown")]
    await Replayer(workflows=[GovernedPublicationWorkflow]).replay_workflow(await handle.fetch_history())
