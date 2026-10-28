"""Sole application writer for immutable 0047 production enrollment."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from psycopg.types.json import Jsonb

from salience.cycles.authority import current_authority
from salience.cycles._production_schedule_contracts import (
    ProductionIntentKey, ProductionScheduleBinding, _APPROVAL_FIELDS,
    _APPROVAL_SCHEMA, _time_text,
)
from salience.cycles._production_schedule_core import ProductionTemporalScheduleControl

class ProductionIntentEnrollmentWriter:
    """Atomically write immutable 0047 enrollment with authorized admission."""

    def __init__(
        self,
        schedule_control: ProductionTemporalScheduleControl,
        binding: ProductionScheduleBinding,
    ) -> None:
        if not isinstance(schedule_control, ProductionTemporalScheduleControl):
            raise ValueError("production schedule control is required")
        if not isinstance(binding, ProductionScheduleBinding):
            raise ValueError("immutable finalized production schedule binding is required")
        self.schedule_control = schedule_control
        self.binding = binding

    def _load_facts(self, connection, key: ProductionIntentKey, *, require_write: bool):
        control = self.schedule_control
        binding = self.binding
        row = control._binding_row(connection, schedule_id=binding.schedule_id)
        current_binding = control._binding_value(row)
        if current_binding != binding:
            raise PermissionError("exact finalized schedule binding is required for enrollment")
        control._validate_binding_route(current_binding)
        if row["finalized_at"] is None:
            raise PermissionError("finalized production inventory is required for enrollment")
        snapshot = binding.member_snapshot
        approval_context = row["request_context"]
        expected_approval = {
            "schema_version": _APPROVAL_SCHEMA,
            "target_alias": binding.target_alias,
            "namespace": binding.namespace,
            "inventory_id": str(binding.inventory_id),
            "inventory_sha256": binding.inventory_sha256,
            "source_kind": binding.source_kind,
            "schedule_id": str(binding.schedule_id),
            "publication_schedule_id": (
                str(binding.publication_schedule_id)
                if binding.publication_schedule_id is not None
                else None
            ),
            "workspace_id": str(binding.workspace_id),
            "goal_id": str(binding.goal_id),
            "goal_revision": binding.goal_revision,
            "source_actor_id": str(binding.source_actor_id),
            "source_owner_subject_id": str(binding.source_owner_subject_id),
            "schedule_operator_subject_id": str(binding.schedule_operator_subject_id),
            "remote_schedule_id": binding.remote_schedule_id,
            "task_queue": snapshot["task_queue"],
            "workflow_type": snapshot["workflow_type"],
            "action_id": snapshot["action_id"],
            "argument_sha256": snapshot["argument_sha256"],
            "route_id": str(binding.route_id),
            "route_tuple_sha256": binding.route_tuple_sha256,
            "slot_id": str(binding.slot_id),
            "owner_id": str(binding.owner_id),
            "fencing_epoch": binding.fencing_epoch,
            "route_manifest_sha256": binding.route_manifest_sha256,
            "artifact_lock_sha256": binding.artifact_lock_sha256,
            "cadence_policy_revision": snapshot["cadence_policy_revision"],
            "first_v4_slot": _time_text(binding.first_v4_slot, "first V4 slot"),
            "history_watermark_sha256": snapshot["history_watermark_sha256"],
            "policy_version_id": str(row["policy_version_id"]),
            "policy_document_hash": row["policy_document_hash"],
        }
        if (
            not isinstance(approval_context, Mapping)
            or frozenset(approval_context) != frozenset(_APPROVAL_FIELDS)
            or any(
                approval_context.get(name) != value
                for name, value in expected_approval.items()
            )
        ):
            raise PermissionError("exact typed source approval is required for enrollment")
        approval = {
            "id": binding.source_approval_id,
            "policy_version_id": row["policy_version_id"],
            "document_hash": row["policy_document_hash"],
        }
        workspace_id = binding.workspace_id
        operator_id = binding.schedule_operator_subject_id
        control._require_current_schedule_authority(
            connection,
            workspace_id=workspace_id,
            goal_id=binding.goal_id,
            operator_id=operator_id,
            approval_id=approval["id"],
            policy_version_id=approval["policy_version_id"],
            policy_document_hash=approval["document_hash"],
        )
        if require_write:
            current_authority(connection, workspace_id, operator_id, "cycles:write")
        source_rows = control._local_source(connection, binding.remote_schedule_id)
        if len(source_rows) != 1 or (
            source_rows[0]["source_kind"],
            source_rows[0]["schedule_id"],
            source_rows[0]["publication_schedule_id"],
            source_rows[0]["workspace_id"],
            source_rows[0]["goal_id"],
            source_rows[0]["goal_revision"],
            source_rows[0]["source_actor_id"],
            source_rows[0]["interval_seconds"],
            source_rows[0]["first_v4_slot"],
        ) != (
            binding.source_kind,
            binding.schedule_id,
            binding.publication_schedule_id,
            binding.workspace_id,
            binding.goal_id,
            binding.goal_revision,
            binding.source_actor_id,
            binding.interval_seconds,
            binding.first_v4_slot,
        ):
            raise PermissionError("current canonical schedule source no longer matches enrollment")
        identity_rows = connection.execute(
            """SELECT id,workspace_id,revision,enabled
               FROM public.identity_subjects
               WHERE id=ANY(%s) ORDER BY id FOR SHARE""",
            ([binding.source_owner_subject_id, operator_id, control.route_lease.owner_id],),
        ).fetchall()
        identity_by_id = {entry["id"]: entry for entry in identity_rows}
        if set(identity_by_id) != {
            binding.source_owner_subject_id,
            operator_id,
            control.route_lease.owner_id,
        } or any(
            identity_by_id[subject_id]["workspace_id"] != workspace_id
            or identity_by_id[subject_id]["enabled"] is not True
            for subject_id in identity_by_id
        ):
            raise PermissionError("current source owner, operator, and route owner identities required")
        if binding.source_owner_subject_id == operator_id:
            raise PermissionError("source owner and schedule operator must differ")
        subject = identity_by_id[operator_id]
        grants = connection.execute(
            """SELECT id,scope,effect FROM public.permission_grants
               WHERE workspace_id=%s AND principal_type='identity'
                 AND principal_id=%s AND constraints='{}'::jsonb
                 AND (expires_at IS NULL OR expires_at>clock_timestamp())
               ORDER BY id FOR SHARE""",
            (workspace_id, str(operator_id)),
        ).fetchall()
        if any(
            grant["effect"] == "deny"
            and grant["scope"] in {"cycles:schedule", "cycles:write"}
            for grant in grants
        ):
            raise PermissionError("current schedule/admission grant is denied")
        grant_ids = [
            str(grant["id"])
            for grant in grants
            if grant["effect"] == "allow"
        ]
        if not grant_ids or not any(
            grant["effect"] == "allow" and grant["scope"] == "cycles:schedule"
            for grant in grants
        ) or require_write and not any(
            grant["effect"] == "allow" and grant["scope"] == "cycles:write"
            for grant in grants
        ):
            raise PermissionError("current canonical schedule/admission grants required")
        stops = connection.execute(
            """SELECT scope_key,revision,stopped FROM public.v4_stop_scopes
               WHERE workspace_id=%s AND scope_key=ANY(%s) FOR SHARE""",
            (workspace_id, ["workspace", "goal:" + str(binding.goal_id)]),
        ).fetchall()
        stop_by_scope = {entry["scope_key"]: entry for entry in stops}
        if set(stop_by_scope) != {"workspace", "goal:" + str(binding.goal_id)} or any(
            entry["stopped"] is not False for entry in stop_by_scope.values()
        ):
            raise PermissionError("current workspace and goal stop state required")
        intent = connection.execute(
            """SELECT intent.id,intent.goal_id,intent.goal_revision,
                      intent.eligibility_revision,goal.workspace_id,
                      goal.revision AS current_goal_revision,goal.state AS goal_state
               FROM public.v4_cycle_intents intent
               JOIN public.v4_goals goal ON goal.id=intent.goal_id
               JOIN public.v4_goal_revisions revision
                 ON revision.goal_id=goal.id AND revision.revision=intent.goal_revision
               WHERE intent.id=%s FOR SHARE OF intent,goal""",
            (key.intent_id,),
        ).fetchone()
        if intent is None:
            raise ValueError("canonical production intent is unavailable")
        if (
            intent["goal_id"] != binding.goal_id
            or intent["workspace_id"] != workspace_id
            or intent["goal_revision"] != binding.goal_revision
            or intent["current_goal_revision"] != binding.goal_revision
            or intent["goal_state"] != "active"
        ):
            raise PermissionError("current bound active goal revision is required")
        if intent["eligibility_revision"] != key.eligibility_revision:
            raise ValueError("canonical intent eligibility revision is stale")
        if control.route_lease.owner_id not in identity_by_id:
            raise PermissionError("current route owner identity required")
        if control.route_lease.route_id != binding.route_id:
            raise PermissionError("current route lease does not match finalized source binding")
        stops_payload = {
            "workspace_stop_revision": stop_by_scope["workspace"]["revision"],
            "goal_stop_revision": stop_by_scope["goal:" + str(binding.goal_id)]["revision"],
        }
        facts = {
            "intent_id": key.intent_id,
            "eligibility_revision": key.eligibility_revision,
            "workspace_id": workspace_id,
            "goal_id": binding.goal_id,
            "goal_revision": binding.goal_revision,
            "source_owner_id": binding.source_owner_subject_id,
            "subject_id": operator_id,
            "subject_revision": subject["revision"],
            "grant_ids": grant_ids,
            "policy_version_id": approval["policy_version_id"],
            "policy_document_hash": approval["document_hash"],
            **stops_payload,
            "artifact_sha256": control.route.artifact_sha256,
            "route_id": binding.route_id,
            "slot_id": binding.slot_id,
            "route_owner_id": binding.owner_id,
            "fencing_epoch": binding.fencing_epoch,
            "route_manifest_sha256": binding.route_manifest_sha256,
            "external_lock_sha256": binding.artifact_lock_sha256,
            "operation_id": key.intent_id,
            "approval_id": approval["id"],
        }
        return facts

    @staticmethod
    def _same_enrollment(existing, facts) -> bool:
        columns = (
            "intent_id",
            "eligibility_revision",
            "workspace_id",
            "goal_id",
            "goal_revision",
            "source_owner_id",
            "subject_id",
            "subject_revision",
            "grant_ids",
            "policy_version_id",
            "policy_document_hash",
            "workspace_stop_revision",
            "goal_stop_revision",
            "artifact_sha256",
            "route_id",
            "slot_id",
            "route_owner_id",
            "fencing_epoch",
            "route_manifest_sha256",
            "external_lock_sha256",
        )
        return all(existing[column] == facts[column] for column in columns)

    def _enroll_in_transaction(
        self,
        connection,
        key: ProductionIntentKey,
        *,
        require_write: bool,
    ):
        if not isinstance(key, ProductionIntentKey):
            raise ValueError("canonical production intent key required")
        control = self.schedule_control
        facts = self._load_facts(connection, key, require_write=require_write)
        decision = control._route_decision(
            route_id=facts["route_id"],
            route_tuple_sha256=self.binding.route_tuple_sha256,
            slot_id=facts["slot_id"],
            owner_id=facts["route_owner_id"],
            fencing_epoch=facts["fencing_epoch"],
            route_manifest_sha256=facts["route_manifest_sha256"],
            artifact_lock_sha256=facts["external_lock_sha256"],
            operation_id=facts["operation_id"],
            permit_id=facts["approval_id"],
            action_kind="schedule_admit",
        )
        if not isinstance(decision.decision_id, UUID):
            raise PermissionError("one fresh exact route-fence decision is required")
        refreshed = self._load_facts(connection, key, require_write=require_write)
        comparable_facts = {name: value for name, value in facts.items() if name not in {"operation_id", "approval_id"}}
        comparable_refreshed = {
            name: value
            for name, value in refreshed.items()
            if name not in {"operation_id", "approval_id"}
        }
        if comparable_refreshed != comparable_facts:
            raise PermissionError("policy, stop, identity, source, or route fact changed before enrollment")
        readback = control.route_authority.read_slot(
            facts["route_id"], facts["slot_id"]
        )
        if (
            not readback.available
            or readback.state != "leased"
            or readback.lease != control.route_lease
            or readback.fencing_epoch != facts["fencing_epoch"]
        ):
            raise PermissionError("exact live route lease readback is required before enrollment commit")
        expected = {
            name: value
            for name, value in facts.items()
            if name not in {"operation_id", "approval_id"}
        }
        existing = connection.execute(
            """SELECT * FROM public.v4_production_intent_enrollments
               WHERE intent_id=%s AND eligibility_revision=%s FOR UPDATE""",
            (key.intent_id, key.eligibility_revision),
        ).fetchone()
        if existing is not None:
            if not self._same_enrollment(existing, expected):
                raise PermissionError("production intent enrollment replay conflicts with current authority")
            return existing
        return connection.execute(
            """INSERT INTO public.v4_production_intent_enrollments(
                   intent_id,eligibility_revision,workspace_id,goal_id,goal_revision,
                   source_owner_id,subject_id,subject_revision,grant_ids,
                   policy_version_id,policy_document_hash,workspace_stop_revision,
                   goal_stop_revision,artifact_sha256,route_id,slot_id,route_owner_id,
                   fencing_epoch,route_manifest_sha256,external_lock_sha256,
                   route_fence_decision_id)
               VALUES(%(intent_id)s,%(eligibility_revision)s,%(workspace_id)s,%(goal_id)s,
                   %(goal_revision)s,%(source_owner_id)s,%(subject_id)s,%(subject_revision)s,
                   %(grant_ids)s,%(policy_version_id)s,%(policy_document_hash)s,
                   %(workspace_stop_revision)s,%(goal_stop_revision)s,%(artifact_sha256)s,
                   %(route_id)s,%(slot_id)s,%(route_owner_id)s,%(fencing_epoch)s,
                   %(route_manifest_sha256)s,%(external_lock_sha256)s,%(route_fence_decision_id)s)
               RETURNING *""",
            expected
            | {
                "grant_ids": Jsonb(expected["grant_ids"]),
                "route_fence_decision_id": decision.decision_id,
            },
        ).fetchone()

    def current_key(self, intent_id: UUID) -> ProductionIntentKey:
        if not isinstance(intent_id, UUID):
            intent_id = UUID(str(intent_id))
        with self.schedule_control._connect() as connection:
            row = connection.execute(
                "SELECT eligibility_revision FROM public.v4_cycle_intents WHERE id=%s",
                (intent_id,),
            ).fetchone()
        if row is None:
            raise ValueError("canonical production intent is unavailable")
        return ProductionIntentKey(intent_id, row["eligibility_revision"])

    def enroll_and_admit(self, key: ProductionIntentKey):
        from salience.cycles.admission import CycleAdmission

        with self.schedule_control._connect() as connection:
            with connection.transaction():
                current_authority(
                    connection,
                    self.binding.workspace_id,
                    self.binding.schedule_operator_subject_id,
                    "cycles:write",
                )
                enrollment = self._enroll_in_transaction(
                    connection, key, require_write=True
                )
                admission_service = CycleAdmission(
                    self.schedule_control.database_url,
                    workspace_id=self.binding.workspace_id,
                    subject_id=self.binding.schedule_operator_subject_id,
                )
                admission = admission_service._admit(connection, key.intent_id)
                current_authority(
                    connection,
                    self.binding.workspace_id,
                    self.binding.schedule_operator_subject_id,
                    "cycles:write",
                )
                return {"enrollment": enrollment, "admission": admission}
