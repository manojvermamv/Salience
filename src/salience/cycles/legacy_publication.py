"""Explicitly approved native fixture publication authority; no implicit account grant."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from salience.cycles.authority import current_authority
from salience.cycles.baselines import resolve_baseline
from salience.cycles.contracts import CycleRequest
from salience.publication.governance import PublicationAuthorizationContext, PublicationAuthorizer
from salience.publication.providers import FixturePublisherAdapter
from salience.publication.repository import PublicationRepository


def require_fixture():
    # Import lazily: LegacyDispatch includes these command methods.
    from salience.cycles.legacy_dispatch import require_fixture as require
    require()


class LegacyPublicationCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    goal_id: UUID
    request: CycleRequest
    ready_package_id: UUID
    publisher_account_id: UUID
    publication_approval_request_id: UUID
    budget_id: UUID
    platform: Literal["fixture"] = "fixture"
    destination: Literal["fixture://account"] = "fixture://account"
    locale: Literal["en"] = "en"
    territory: Literal["global"] = "global"
    visibility: Literal["private"] = "private"
    capability_profile_version: Literal[1] = 1
    contract_version: Literal["LegacyPublicationDispatch.local.v1"] = "LegacyPublicationDispatch.local.v1"

    def native_payload(self):
        return {key: value for key, value in self.model_dump(mode="json").items()
                if key not in {"goal_id", "request", "contract_version"}} | {"contract_version": "PublicationWorkflowRequest@v1"}


def account_scope(account_id):
    return "legacy:publication:account:"+str(UUID(str(account_id)))


def require_publication_account(connection, bridge, goal, spec, account_id):
    current_authority(connection, bridge.workspace_id, bridge.subject_id, "legacy:publication")
    current_authority(connection, bridge.workspace_id, bridge.subject_id, account_scope(account_id))
    baseline = resolve_baseline(connection, goal["id"], goal["revision"])
    mapping = connection.execute("SELECT * FROM v4_legacy_publication_accounts WHERE goal_id=%s AND goal_revision=%s AND workspace_id=%s", (goal["id"],goal["revision"],bridge.workspace_id)).fetchone()
    if not baseline or not mapping or mapping["publisher_account_id"] != UUID(str(account_id)) or mapping["content_program_id"] != spec.content_program_id or mapping["baseline_approval_id"] != baseline["approval_id"]:
        raise PermissionError("original approved publication goal/account binding required")
    account = connection.execute("SELECT account.*,to_jsonb(account)-ARRAY['status','updated_at'] AS identity FROM publisher_accounts account WHERE id=%s AND workspace_id=%s FOR SHARE",(account_id,bridge.workspace_id)).fetchone()
    if not account or account["identity"] != mapping["account_identity"] or account["status"] != "active" or account["platform"] != "fixture" or account["account_type"] != "creator" or not account["external_account_reference"].startswith("fixture:"):
        raise PermissionError("current original fixture publication account required")
    return mapping


def require_publication_proposal(connection, bridge, goal, spec, payload):
    require_publication_account(connection,bridge,goal,spec,payload["publisher_account_id"])
    # Zero limit and current status are native facts; the synthetic publisher
    # reserves/settles zero. A positive budget cannot expand fixture authority.
    budget = connection.execute("SELECT * FROM budgets WHERE id=%s AND workspace_id=%s AND content_program_id=%s FOR SHARE",(payload["budget_id"],bridge.workspace_id,spec.content_program_id)).fetchone()
    if not budget or budget["scope"] != "publication" or budget["status"] != "active" or budget["limit_amount"] != 0:
        raise PermissionError("current original zero-cost publication budget required")
    current = PublicationRepository(bridge.database_url).proposed_authorization(connection, payload | {
        "workspace_id":str(bridge.workspace_id),"content_program_id":str(spec.content_program_id),"idempotency_key":"legacy-proposal-only"})
    decision = publication_decision(current, budget_status="reserved")
    if not decision.allowed:
        raise PermissionError("current native publication authorization required: "+",".join(decision.reasons))
    return current


def require_publication_reconciliation(bridge, binding):
    """Revalidate the immutable publication source under its separate readback grant.

    This deliberately does not renew or extend the original execution permit. It
    is usable only for an already materialized publication operation and still
    rechecks the same goal revision, account mapping, baseline, native approval,
    profile, policy, and rights before a fixture receipt can be read back.
    """
    require_fixture()
    from psycopg.rows import dict_row
    import psycopg

    if (
        binding.get("stage") != "publication"
        or binding.get("workspace_id") != bridge.workspace_id
        or binding.get("actor_id") != bridge.subject_id
    ):
        raise PermissionError("original scoped publication reconciliation required")
    account_id = UUID(str(binding["payload"]["publisher_account_id"]))
    with psycopg.connect(bridge.database_url, row_factory=dict_row, connect_timeout=3) as connection:
        connection.execute("SET LOCAL statement_timeout='3s'")
        current_authority(
            connection,
            bridge.workspace_id,
            bridge.subject_id,
            "legacy:publication:reconcile:account:" + str(account_id),
        )
        goal, spec = bridge._policy_goal(connection, binding["goal_id"])
        original = connection.execute(
            "SELECT goal_revision FROM v4_cycle_intents WHERE id=%s AND goal_id=%s",
            (binding["intent_id"], binding["goal_id"]),
        ).fetchone()
        if (
            not original
            or goal["revision"] != original["goal_revision"]
            or spec.content_program_id != binding["content_program_id"]
        ):
            raise PermissionError("original approved publication revision required for reconciliation")
        require_publication_proposal(
            connection, bridge, goal, spec, binding["payload"]
        )


def publication_decision(current, *, budget_status):
    request=current.request
    expected=FixturePublisherAdapter().capabilities
    if current.profile != expected:
        raise PermissionError("current exact fixture publication profile required")
    context=PublicationAuthorizationContext(request=request,profile=current.profile,
        ready_package_id=request.ready_package_id,ready_package_workspace_id=request.workspace_id,
        ready_package_program_id=request.content_program_id,ready_package_approval_state=current.ready_package_approval_state,
        account_workspace_id=current.account_workspace_id,connection_account_id=current.connection_account_id or "missing",
        account_type=current.account_type,account_status=current.account_status,connection_status=current.connection_status or "missing",
        connection_scopes=current.connection_scopes,content_type="video",authorized_destination="fixture://account",
        authorized_locale="en",authorized_territory="global",authorized_visibility="private",policy_allowed=current.policy_allowed,
        rights_allowed=current.rights_allowed,disclosure_allowed=current.disclosure_status=="approved",
        publishing_approval_state=current.publication_approval_state,budget_status=budget_status,
        rate_quota_available=True,capability_profile_version=1)
    return PublicationAuthorizer().evaluate(context)


class LegacyPublicationCommands:
    def bind_publication_account(self, goal_id, *, expected_revision, publisher_account_id):
        require_fixture()
        publisher_account_id=UUID(str(publisher_account_id))
        if type(expected_revision) is not int or expected_revision<1:
            raise ValueError("current goal revision required")
        with self._command("goals:approve") as connection:
            goal,spec=self._policy_goal(connection,goal_id)
            current_authority(connection,self.workspace_id,self.subject_id,"legacy:publication")
            current_authority(connection,self.workspace_id,self.subject_id,account_scope(publisher_account_id))
            from salience.cycles.governance import CycleGovernance
            CycleGovernance(self.database_url,workspace_id=self.workspace_id,subject_id=self.subject_id)._require_running(connection,goal_id)
            baseline=resolve_baseline(connection,goal_id,expected_revision)
            if goal["revision"] != expected_revision or goal["state"] != "active" or not baseline:
                raise PermissionError("current approved publication goal required")
            account=connection.execute("SELECT account.*,to_jsonb(account)-ARRAY['status','updated_at'] AS identity FROM publisher_accounts account WHERE id=%s AND workspace_id=%s FOR SHARE",(publisher_account_id,self.workspace_id)).fetchone()
            if not account or account["platform"]!="fixture" or account["account_type"]!="creator" or account["status"]!="active" or not account["external_account_reference"].startswith("fixture:"):
                raise PermissionError("current scoped native fixture account required")
            prior=connection.execute("SELECT * FROM v4_legacy_publication_accounts WHERE goal_id=%s AND goal_revision=%s",(goal_id,expected_revision)).fetchone()
            if prior:
                if prior["publisher_account_id"]!=publisher_account_id or prior["baseline_approval_id"]!=baseline["approval_id"] or prior["account_identity"]!=account["identity"]:
                    raise ValueError("original publication account mapping conflict")
            else:
                connection.execute("INSERT INTO v4_legacy_publication_accounts(goal_id,goal_revision,workspace_id,content_program_id,publisher_account_id,actor_id,baseline_approval_id,account_ref,account_identity) VALUES(%s,%s,%s,%s,%s,%s,%s,'fixture-account',%s)",(goal_id,expected_revision,self.workspace_id,spec.content_program_id,publisher_account_id,self.subject_id,baseline["approval_id"],Jsonb(account["identity"])))
                self._event(connection,goal_id,"legacy_publication_account_approved",{"goal_revision":expected_revision,"publisher_account_id":str(publisher_account_id),"baseline_approval_id":str(baseline["approval_id"])})
            require_publication_account(connection,self,goal,spec,publisher_account_id)
            return {"goal_id":str(goal_id),"goal_revision":expected_revision,"publisher_account_id":str(publisher_account_id),"account_ref":"fixture-account","state":"bound"}

    def submit_publication(self, command):
        require_fixture()
        command=LegacyPublicationCommand.model_validate(command)
        with self._command("cycles:write") as connection:
            goal,spec=self._policy_goal(connection,command.goal_id)
            require_publication_proposal(connection,self,goal,spec,command.native_payload())
            result=self._submit(connection,command)
            require_publication_proposal(connection,self,goal,spec,command.native_payload())
            return result | {"dry_run":False,"effects_enabled":False,"execution_profile":"controlled-fixture-publication"}

    def bind_publication_schedule(self, goal_id, *, expected_revision):
        require_fixture()
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("current goal revision required")
        with self._command("cycles:schedule") as connection:
            current_authority(connection, self.workspace_id, self.subject_id, "cycles:schedule")
            goal, spec = self._policy_goal(connection, goal_id)
            from salience.cycles.governance import CycleGovernance

            CycleGovernance(
                self.database_url,
                workspace_id=self.workspace_id,
                subject_id=self.subject_id,
            )._require_running(connection, goal_id)
            cutover = connection.execute(
                "SELECT * FROM v4_schedule_cutovers WHERE goal_id=%s AND workspace_id=%s FOR UPDATE",
                (goal_id, self.workspace_id),
            ).fetchone()
            if (
                not cutover
                or cutover["actor_id"] != self.subject_id
                or cutover["goal_revision"] != expected_revision
                or goal["revision"] != expected_revision
                or goal["state"] != "active"
            ):
                raise PermissionError("original pending publication schedule cutover required")

            from salience.cycles.native_publication_schedules import (
                load_native_publication_source,
                original_publication_schedule,
            )
            from salience.cycles.native_schedules import require_native_binding, schedule_row

            source = load_native_publication_source(
                connection,
                cutover["legacy_schedule_id"],
                self.workspace_id,
                lock=True,
            )
            schedule = schedule_row(
                connection,
                cutover["legacy_schedule_id"],
                self.workspace_id,
                lock=True,
                temporal=True,
            )
            require_native_binding(
                schedule,
                goal_id=goal_id,
                actor_id=self.subject_id,
                task_queue=self.task_queue,
                first_v4_slot=cutover["first_v4_slot"],
            )
            if (
                source["goal_id"] != goal_id
                or source["workspace_id"] != self.workspace_id
                or source["actor_id"] != self.subject_id
                or source["goal_revision"] != expected_revision
                or source["task_queue"] != self.task_queue
                or source["first_v4_slot"] != cutover["first_v4_slot"]
                or schedule["content_program_id"] != spec.content_program_id
            ):
                raise PermissionError("original governed-publication schedule binding required")
            original = original_publication_schedule(
                connection,
                cutover["legacy_schedule_id"],
                self.workspace_id,
                publication_schedule_id=source["publication_schedule_id"],
                lock=True,
            )
            require_publication_proposal(
                connection, self, goal, spec, original["command_payload"]
            )

            prior = connection.execute(
                "SELECT * FROM v4_legacy_publication_schedule_plans WHERE goal_id=%s",
                (goal_id,),
            ).fetchone()
            expected = (
                self.workspace_id,
                self.subject_id,
                expected_revision,
                cutover["legacy_schedule_id"],
                source["publication_schedule_id"],
                self.task_queue,
            )
            if prior:
                found = (
                    prior["workspace_id"],
                    prior["actor_id"],
                    prior["goal_revision"],
                    prior["schedule_id"],
                    prior["publication_schedule_id"],
                    prior["task_queue"],
                )
                if found != expected:
                    raise ValueError("original publication schedule plan conflict")
                return {
                    "goal_id": str(goal_id),
                    "schedule_id": str(cutover["legacy_schedule_id"]),
                    "publication_schedule_id": str(source["publication_schedule_id"]),
                    "state": "bound",
                }
            if cutover["state"] != "pending":
                raise ValueError("bind publication schedule after prepare and before activation")
            connection.execute(
                """INSERT INTO v4_legacy_publication_schedule_plans
                    (goal_id,workspace_id,actor_id,goal_revision,schedule_id,publication_schedule_id,task_queue)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (goal_id, *expected),
            )
            self._event(
                connection,
                goal_id,
                "legacy_publication_schedule_bound",
                {
                    "goal_revision": expected_revision,
                    "schedule_id": str(cutover["legacy_schedule_id"]),
                    "publication_schedule_id": str(source["publication_schedule_id"]),
                    "task_queue": self.task_queue,
                },
            )
            current_authority(connection, self.workspace_id, self.subject_id, "cycles:schedule")
            return {
                "goal_id": str(goal_id),
                "schedule_id": str(cutover["legacy_schedule_id"]),
                "publication_schedule_id": str(source["publication_schedule_id"]),
                "state": "bound",
            }

    def admit_publication_scheduled(self, intent_id):
        require_fixture()
        with self._command("cycles:schedule") as connection:
            current_authority(connection, self.workspace_id, self.subject_id, "cycles:schedule")
            goal, spec, intent = self._intent(connection, intent_id)
            cutover = connection.execute(
                "SELECT * FROM v4_schedule_cutovers WHERE goal_id=%s AND workspace_id=%s FOR UPDATE",
                (goal["id"], self.workspace_id),
            ).fetchone()
            plan = connection.execute(
                "SELECT * FROM v4_legacy_publication_schedule_plans WHERE goal_id=%s",
                (goal["id"],),
            ).fetchone()
            if (
                not cutover
                or not plan
                or cutover["state"] != "active"
                or plan["actor_id"] != self.subject_id
                or plan["task_queue"] != self.task_queue
                or plan["schedule_id"] != cutover["legacy_schedule_id"]
                or plan["workspace_id"] != self.workspace_id
                or plan["goal_revision"] != intent["goal_revision"]
                or intent["due_at"] < cutover["first_v4_slot"]
            ):
                raise PermissionError("current original active publication schedule required")

            from salience.cycles.native_publication_schedules import load_native_publication_source
            from salience.cycles.native_schedules import require_native_binding, schedule_row

            schedule = schedule_row(
                connection,
                plan["schedule_id"],
                self.workspace_id,
                lock=True,
                temporal=True,
            )
            require_native_binding(
                schedule,
                goal_id=goal["id"],
                actor_id=self.subject_id,
                task_queue=self.task_queue,
                first_v4_slot=cutover["first_v4_slot"],
            )
            source = load_native_publication_source(
                connection, plan["schedule_id"], self.workspace_id
            )
            if (
                source["publication_schedule_id"] != plan["publication_schedule_id"]
                or source["goal_id"] != goal["id"]
                or source["goal_revision"] != intent["goal_revision"]
                or schedule["status"] != "paused"
                or schedule["content_program_id"] != spec.content_program_id
            ):
                raise PermissionError("original paused publication schedule and request required")
            original = source["original_source"]
            require_publication_proposal(
                connection, self, goal, spec, original["command_payload"]
            )
            result = self._admit(connection, intent_id)
            if result["cycle_id"]:
                request = CycleRequest(
                    origin="scheduled",
                    expected_revision=intent["goal_revision"],
                    slot_time=intent["due_at"],
                    idempotency_key=(
                        "legacy-publication-schedule:"
                        + str(plan["publication_schedule_id"])
                        + ":"
                        + str(intent_id)
                    ),
                )
                command = LegacyPublicationCommand(
                    goal_id=goal["id"],
                    request=request,
                    **{
                        key: value
                        for key, value in original["command_payload"].items()
                        if key != "contract_version"
                    },
                )
                self._materialize(connection, command, spec, result["cycle_id"])
                require_publication_proposal(
                    connection, self, goal, spec, original["command_payload"]
                )
            current_authority(connection, self.workspace_id, self.subject_id, "cycles:schedule")
            return result

    def admit_legacy_publication_tick(
        self,
        goal_id,
        *,
        schedule_id,
        publication_schedule_id,
        scheduled_at,
        remote_id,
    ):
        """Commit one resumed original publication tick through the canonical outbox."""
        require_fixture()
        if not isinstance(scheduled_at, datetime) or scheduled_at.tzinfo is None or scheduled_at.microsecond:
            raise ValueError("whole-second scheduled publication slot required")
        scheduled_at = scheduled_at.astimezone(timezone.utc)
        goal_id = UUID(str(goal_id))
        schedule_id = UUID(str(schedule_id))
        publication_schedule_id = UUID(str(publication_schedule_id))
        with self._command("cycles:schedule") as connection:
            current_authority(connection, self.workspace_id, self.subject_id, "cycles:schedule")
            goal, spec = self._policy_goal(connection, goal_id)
            from salience.cycles.governance import CycleGovernance

            CycleGovernance(
                self.database_url,
                workspace_id=self.workspace_id,
                subject_id=self.subject_id,
            )._require_running(connection, goal_id)
            cutover = connection.execute(
                "SELECT * FROM v4_schedule_cutovers WHERE goal_id=%s AND workspace_id=%s FOR UPDATE",
                (goal_id, self.workspace_id),
            ).fetchone()
            plan = connection.execute(
                "SELECT * FROM v4_legacy_publication_schedule_plans WHERE goal_id=%s",
                (goal_id,),
            ).fetchone()
            if (
                not cutover
                or cutover["legacy_schedule_id"] != schedule_id
                or not plan
                or plan["actor_id"] != self.subject_id
                or plan["task_queue"] != self.task_queue
                or plan["schedule_id"] != schedule_id
                or plan["publication_schedule_id"] != publication_schedule_id
            ):
                raise PermissionError("original scoped publication schedule plan required")
            if cutover["state"] == "rollback_pending":
                raise RuntimeError("canonical publication rollback readback still pending")
            if cutover["state"] != "rolled_back" or scheduled_at <= cutover["rollback_after_slot"]:
                raise PermissionError("completed publication rollback and later scheduled slot required")

            from salience.cycles.native_publication_schedules import load_native_publication_source
            from salience.cycles.native_schedules import require_native_binding, schedule_metadata, schedule_row

            schedule = schedule_row(
                connection, schedule_id, self.workspace_id, lock=True, temporal=True
            )
            require_native_binding(
                schedule,
                goal_id=goal_id,
                actor_id=self.subject_id,
                task_queue=self.task_queue,
                first_v4_slot=cutover["first_v4_slot"],
            )
            source = load_native_publication_source(connection, schedule_id, self.workspace_id)
            metadata = schedule_metadata(schedule)
            if (
                source["publication_schedule_id"] != publication_schedule_id
                or source["goal_id"] != goal_id
                or source["actor_id"] != self.subject_id
                or source["goal_revision"] != cutover["goal_revision"]
                or source["task_queue"] != self.task_queue
                or source["first_v4_slot"] != cutover["first_v4_slot"]
            ):
                raise PermissionError("original publication schedule identity required")
            expected_remote = "publication:" + str(publication_schedule_id)
            if (
                remote_id != expected_remote
                or metadata["remote_id"] != expected_remote
                or schedule["status"] != "active"
                or schedule["content_program_id"] != spec.content_program_id
                or metadata["interval_seconds"] != spec.cadence.interval_seconds
                or metadata["resume_after"] != cutover["rollback_after_slot"].isoformat()
                or metadata["next_run_at"] is None
                or scheduled_at < metadata["next_run_at"]
                or (scheduled_at - metadata["next_run_at"]).total_seconds()
                % metadata["interval_seconds"]
            ):
                raise PermissionError("current exact resumed publication schedule required")
            original = source["original_source"]
            require_publication_proposal(
                connection, self, goal, spec, original["command_payload"]
            )
            request = CycleRequest(
                origin="scheduled",
                expected_revision=goal["revision"],
                slot_time=scheduled_at,
                idempotency_key=(
                    "legacy-temporal-publication:"
                    + str(publication_schedule_id)
                    + ":"
                    + scheduled_at.isoformat()
                ),
            )
            command = LegacyPublicationCommand(
                goal_id=goal_id,
                request=request,
                **{
                    key: value
                    for key, value in original["command_payload"].items()
                    if key != "contract_version"
                },
            )
            result = self._submit(connection, command, cutover_poll=True)
            require_publication_proposal(
                connection, self, goal, spec, original["command_payload"]
            )
            connection.execute(
                """UPDATE v4_native_publication_schedule_progress
                    SET last_slot=%s,updated_at=clock_timestamp()
                    WHERE schedule_id=%s AND (last_slot IS NULL OR last_slot<%s)""",
                (scheduled_at, schedule_id, scheduled_at),
            )
            current_authority(connection, self.workspace_id, self.subject_id, "cycles:schedule")
            return result


def require_current_publication(outbox, binding):
    from salience.cycles.legacy_dispatch import LegacyDispatch
    bridge=LegacyDispatch(outbox.database_url,workspace_id=outbox.workspace_id,subject_id=binding['actor_id'],task_queue=binding['task_queue'])
    with bridge._command('cycles:permit') as connection:
        goal,spec=bridge._policy_goal(connection,binding['goal_id'])
        require_publication_proposal(connection,bridge,goal,spec,binding['payload'])


def native_publication_request(binding):
    from salience.workflows.publication import PublicationWorkflowRequest
    payload={key:value for key,value in binding['payload'].items() if key!='contract_version'}
    return PublicationWorkflowRequest(workspace_id=str(binding['workspace_id']),content_program_id=str(binding['content_program_id']),idempotency_key='v4-legacy:'+str(binding['operation_id']),**payload)
