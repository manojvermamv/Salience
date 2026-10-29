"""Shared authority and dependency core for production schedule control."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from salience.cycles.authority import current_authority
from salience.cycles.production_dispatch import RouteFenceDecision, RouteFenceRequest
from salience.cycles.production_routing import (
    ProductionRouteManifestV1, RouteLease, RouteLeaseAuthority, route_manifest_sha256,
)
from salience.cycles.schedule_cutover import FixtureLegacyScheduleControl
from salience.cycles._production_schedule_contracts import (
    ScheduleAlertSink, TemporalScheduleTarget, _text,
)
from salience.cycles._production_schedule_inventory import _ProductionScheduleInventoryWriter
from salience.cycles._production_schedule_target import _ProductionScheduleTargetAdapter

class ProductionTemporalScheduleControl(
    _ProductionScheduleInventoryWriter,
    _ProductionScheduleTargetAdapter,
    FixtureLegacyScheduleControl,
):
    """Restricted writer and exact-identity controller for one injected target."""

    supports_temporal = True

    def __init__(
        self,
        database_url: str,
        *,
        workspace_id: UUID,
        target_alias: str,
        namespace: str,
        route: ProductionRouteManifestV1,
        route_lease: RouteLease,
        route_authority: RouteLeaseAuthority,
        target: TemporalScheduleTarget,
        alerts: ScheduleAlertSink,
    ) -> None:
        _text(database_url, "production schedule database URL")
        if not isinstance(workspace_id, UUID):
            raise ValueError("schedule workspace UUID required")
        if not isinstance(route, ProductionRouteManifestV1) or not isinstance(route_lease, RouteLease):
            raise ValueError("authenticated route manifest and current route lease required")
        if (
            route_lease.route_id != route.route_id
            or route_lease.route_manifest_sha256 != route_manifest_sha256(route)
            or route_lease.artifact_lock_sha256 != route.artifact_lock_sha256
            or route_lease.slot_id not in route.slots
        ):
            raise PermissionError("current lease must match the authenticated route manifest")
        if (
            target_alias != route.route_tuple.temporal_endpoint_id
            or namespace != route.route_tuple.namespace
        ):
            raise PermissionError("exact route target and namespace required")
        for name, value in (("target", target), ("alerts", alerts)):
            if value is None:
                raise ValueError(f"injected {name} required")
        if not callable(getattr(route_authority, "authorize_action", None)) or not callable(
            getattr(route_authority, "read_slot", None)
        ):
            raise ValueError("route lease and action-fence authority required")
        if not isinstance(target, TemporalScheduleTarget):
            raise ValueError("scoped Temporal schedule target protocol required")
        if not isinstance(alerts, ScheduleAlertSink):
            raise ValueError("schedule alert sink protocol required")
        super().__init__(database_url, workspace_id=workspace_id)
        self.workspace_id = workspace_id
        self.target_alias = target_alias
        self.namespace = namespace
        self.route = route
        self.route_lease = route_lease
        self.route_authority = route_authority
        self.target = target
        self.alerts = alerts


    def _connect(self):
        return psycopg.connect(
            self.database_url, row_factory=dict_row, connect_timeout=3
        )


    def _local_source(
        self,
        connection,
        remote_schedule_id: str,
        *,
        schedule_statuses=("active",),
    ):
        statuses = tuple(schedule_statuses)
        if not statuses or any(status not in {"active", "paused"} for status in statuses):
            raise ValueError("current active or paused local schedule status required")
        return connection.execute(
            """SELECT 'intelligence_research'::text AS source_kind,
                      source.schedule_id, NULL::uuid AS publication_schedule_id,
                      source.remote_id, source.workspace_id, source.goal_id,
                      source.goal_revision, source.actor_id AS source_actor_id,
                      source.interval_seconds, source.first_v4_slot,
                      source.observed_last_slot
               FROM public.v4_native_schedule_sources source
               JOIN public.job_schedules schedule
                 ON schedule.id=source.schedule_id AND schedule.workspace_id=source.workspace_id
               JOIN public.v4_goals goal
                 ON goal.id=source.goal_id AND goal.workspace_id=source.workspace_id
               WHERE source.remote_id=%s AND schedule.status=ANY(%s)
                 AND goal.state='active' AND goal.revision=source.goal_revision
               UNION ALL
               SELECT 'governed_publication'::text, source.schedule_id,
                      source.publication_schedule_id, source.remote_id,
                      source.workspace_id, source.goal_id, source.goal_revision,
                      source.actor_id, source.interval_seconds, source.first_v4_slot,
                      source.observed_last_slot
               FROM public.v4_native_publication_schedule_sources source
               JOIN public.job_schedules schedule
                 ON schedule.id=source.schedule_id AND schedule.workspace_id=source.workspace_id
               JOIN public.publication_schedules publication
                 ON publication.id=source.publication_schedule_id
                AND publication.job_schedule_id=source.schedule_id
               JOIN public.v4_goals goal
                 ON goal.id=source.goal_id AND goal.workspace_id=source.workspace_id
               WHERE source.remote_id=%s AND schedule.status=ANY(%s)
                 AND publication.status='scheduled' AND goal.state='active'
                 AND goal.revision=source.goal_revision""",
            (
                remote_schedule_id,
                list(statuses),
                remote_schedule_id,
                list(statuses),
            ),
        ).fetchall()


    def _require_current_schedule_authority(
        self,
        connection,
        *,
        workspace_id: UUID,
        goal_id: UUID,
        operator_id: UUID,
        approval_id: UUID,
        policy_version_id: UUID,
        policy_document_hash: str,
        allow_committed_stop: bool = False,
        expected_request_context: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None:
        current_authority(connection, workspace_id, operator_id, "cycles:schedule")
        policy = connection.execute(
            """SELECT id,workspace_id,status,document_hash FROM public.policy_versions
               WHERE id=%s AND workspace_id=%s FOR SHARE""",
            (policy_version_id, workspace_id),
        ).fetchone()
        approval = connection.execute(
            """SELECT id,workspace_id,policy_version_id,effect_type,
                      status,decided_by,decided_at,request_context,
                      (request_context->>'expires_at')::timestamptz AS expires_at
               FROM public.approval_requests
               WHERE id=%s AND workspace_id=%s AND policy_version_id=%s
                 AND effect_type='production_schedule_binding' FOR SHARE""",
            (approval_id, workspace_id, policy_version_id),
        ).fetchone()
        valid_approval_statuses = {"approved"}
        if allow_committed_stop:
            # A committed stop is an emergency-only path. A decided approval
            # that has since been revoked or expired still identifies the
            # immutable binding, but cannot authorize forward control.
            valid_approval_statuses.update({"revoked", "expired"})
        if (
            policy is None
            or policy["document_hash"] != policy_document_hash
            or approval is None
            or approval["status"] not in valid_approval_statuses
            or approval["decided_by"] is None
            or approval["decided_at"] is None
            or approval["expires_at"] is None
            or not isinstance(approval["request_context"], Mapping)
            or approval["request_context"].get("policy_document_hash") != policy_document_hash
        ):
            raise PermissionError("immutable schedule policy and approval binding are required")
        if expected_request_context is not None:
            request_context = approval["request_context"]
            if (
                not isinstance(request_context, Mapping)
                or set(request_context) != set(expected_request_context)
                or any(
                    request_context.get(key) != value
                    for key, value in expected_request_context.items()
                )
            ):
                raise PermissionError("immutable schedule approval identity is inconsistent")
        stops = connection.execute(
            """SELECT scope_key,revision,stopped,changed_by
               FROM public.v4_stop_scopes
               WHERE workspace_id=%s AND scope_key=ANY(%s) FOR SHARE""",
            (workspace_id, ["workspace", "goal:" + str(goal_id)]),
        ).fetchall()
        stop_by_scope = {row["scope_key"]: row for row in stops}
        if set(stop_by_scope) != {"workspace", "goal:" + str(goal_id)}:
            raise PermissionError("current workspace and goal stop scopes are required")

        stopped_rows = [row for row in stops if row["stopped"] is True]
        if stopped_rows and not allow_committed_stop:
            raise PermissionError("current workspace and goal stops block schedule control")

        stop_grant_ids: dict[str, tuple[UUID, ...]] = {}
        for row in stopped_rows:
            stop_actor_id = row["changed_by"]
            if not isinstance(stop_actor_id, UUID):
                raise PermissionError("current committed stop actor is required")
            current_authority(
                connection, workspace_id, stop_actor_id, "cycles:stop"
            )
            receipts = connection.execute(
                """SELECT id FROM public.v4_stop_commands
                   WHERE workspace_id=%s AND scope_key=%s
                     AND resulting_revision=%s AND stopped IS TRUE
                     AND actor_id=%s
                   ORDER BY id FOR SHARE""",
                (
                    workspace_id,
                    row["scope_key"],
                    row["revision"],
                    stop_actor_id,
                ),
            ).fetchall()
            if not receipts:
                raise PermissionError("exact current committed stop receipt required")
            grants = connection.execute(
                """SELECT id FROM public.permission_grants
                   WHERE workspace_id=%s AND principal_type='identity'
                     AND principal_id=%s AND scope='cycles:stop'
                     AND effect='allow' AND constraints='{}'::jsonb
                     AND expires_at>clock_timestamp()
                   ORDER BY id FOR SHARE""",
                (workspace_id, str(stop_actor_id)),
            ).fetchall()
            if not grants:
                raise PermissionError("current cycles:stop grant required")
            stop_grant_ids[row["scope_key"]] = tuple(
                grant["id"] for grant in grants
            )

        grants = connection.execute(
            """SELECT id FROM public.permission_grants
               WHERE workspace_id=%s AND principal_type='identity' AND principal_id=%s
                 AND scope='cycles:schedule' AND effect='allow'
                 AND constraints='{}'::jsonb
                 AND (expires_at IS NULL OR expires_at>clock_timestamp())
               ORDER BY id FOR SHARE""",
            (workspace_id, str(operator_id)),
        ).fetchall()
        if not grants:
            raise PermissionError("current cycles:schedule grant required")
        schedule_grants = tuple(grant["id"] for grant in grants)
        if stopped_rows:
            return {
                "schedule_grant_ids": tuple(str(grant_id) for grant_id in schedule_grants),
                "scopes": tuple(
                    (
                        row["scope_key"],
                        row["revision"],
                        row["stopped"],
                        str(row["changed_by"]) if row["changed_by"] is not None else None,
                        tuple(
                            str(grant_id)
                            for grant_id in stop_grant_ids.get(row["scope_key"], ())
                        ),
                    )
                    for row in sorted(stops, key=lambda item: item["scope_key"])
                ),
            }

        if any(row["stopped"] is not False for row in stop_by_scope.values()):
            raise PermissionError("current workspace and goal stops block schedule control")
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        if (
            approval["status"] != "approved"
            or policy["status"] != "active"
            or approval["expires_at"] <= now
        ):
            raise PermissionError("current schedule policy and approval are required")
        return None


    def _route_decision(
        self,
        *,
        route_id: UUID,
        route_tuple_sha256: str,
        slot_id: UUID,
        owner_id: UUID,
        fencing_epoch: int,
        route_manifest_sha256: str,
        artifact_lock_sha256: str,
        operation_id: UUID,
        permit_id: UUID,
        action_kind: str,
    ) -> RouteFenceDecision:
        request = RouteFenceRequest(
            deployment_id=self.route.route_tuple.deployment_id,
            environment_id=self.route.route_tuple.environment_id,
            route_id=route_id,
            slot_id=slot_id,
            owner_id=owner_id,
            fencing_epoch=fencing_epoch,
            route_manifest_sha256=route_manifest_sha256,
            artifact_lock_sha256=artifact_lock_sha256,
            operation_id=operation_id,
            permit_id=permit_id,
            action_kind=action_kind,
            route_tuple_sha256=route_tuple_sha256,
        )
        decision = self.route_authority.authorize_action(request)
        if (
            not isinstance(decision, RouteFenceDecision)
            or not decision.authorized
            or decision.request != request
            or decision.request.route_tuple_sha256 != request.route_tuple_sha256
        ):
            raise PermissionError("fresh exact route-fence decision required")
        readback = self.route_authority.read_slot(route_id, slot_id)
        if (
            not readback.available
            or readback.state != "leased"
            or readback.lease != self.route_lease
            or readback.fencing_epoch != fencing_epoch
        ):
            raise PermissionError("current route owner and fencing epoch readback required")
        return decision
