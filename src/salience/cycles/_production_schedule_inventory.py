"""Database-backed 0048 inventory, receipt, and source-binding writer."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from uuid import UUID

from psycopg.types.json import Jsonb

from salience.cycles._production_schedule_contracts import (
    _APPROVAL_FIELDS, _APPROVAL_SCHEMA, _INVENTORY_SCHEMA, _MEMBER_KEYS,
    _MEMBER_SCHEMA, _aware, _digest, _text, _time_text,
    ProductionInventoryReceipt, ProductionScheduleBinding, TemporalScheduleAction,
    TemporalScheduleDescriptor, TemporalScheduleHistoryPage, TemporalScheduleInventoryPage,
)

class _ProductionScheduleHashes:
    @staticmethod
    def jsonb(connection, value: object) -> str:
        row = connection.execute(
            "SELECT public.v4_production_schedule_jsonb_sha256_v1(%s::jsonb) AS digest",
            (Jsonb(value),),
        ).fetchone()
        return _digest(row["digest"], "database JSONB digest")

    @staticmethod
    def text(connection, value: str) -> str:
        row = connection.execute(
            "SELECT public.v4_production_schedule_text_sha256_v1(%s) AS digest",
            (value,),
        ).fetchone()
        return _digest(row["digest"], "database text digest")

class _ProductionScheduleInventoryWriter:
    """Mixin for normalized inventory capture/finalize/binding operations."""

    @staticmethod
    def _cursor_hash(connection, cursor: str | None, start: str) -> str:
        if cursor is None:
            return _ProductionScheduleHashes.text(connection, start)
        return _ProductionScheduleHashes.text(connection, cursor)


    @staticmethod
    def _page_limit(page_count: int) -> None:
        if page_count >= 10_000:
            raise ValueError("production schedule inventory page bound exceeded")


    def _route_identity(self, descriptor: TemporalScheduleDescriptor) -> None:
        expected = (
            self.route.route_id,
            self.route.route_tuple_sha256,
            self.route_lease.slot_id,
            self.route_lease.owner_id,
            self.route_lease.fencing_epoch,
            self.route_lease.route_manifest_sha256,
            self.route_lease.artifact_lock_sha256,
        )
        actual = (
            descriptor.route_id,
            descriptor.route_tuple_sha256,
            descriptor.slot_id,
            descriptor.owner_id,
            descriptor.fencing_epoch,
            descriptor.route_manifest_sha256,
            descriptor.artifact_lock_sha256,
        )
        if actual != expected or descriptor.task_queue != self.route.route_tuple.task_queue:
            raise PermissionError("inventory route, owner, slot, epoch, lock, or queue mismatch")


    def _member_snapshot(
        self,
        connection,
        descriptor: TemporalScheduleDescriptor,
        history_pages: list[dict[str, object]],
        all_actions: list[TemporalScheduleAction],
    ) -> tuple[dict[str, object], dict[str, object]]:
        sources = self._local_source(connection, descriptor.remote_schedule_id)
        if descriptor.classification == "bound":
            self._route_identity(descriptor)
            if len(sources) != 1:
                raise PermissionError("bound remote schedule requires one current canonical source")
            source = sources[0]
            source_identity = (
                source["source_kind"],
                source["schedule_id"],
                source["publication_schedule_id"],
                source["workspace_id"],
                source["goal_id"],
                source["goal_revision"],
                source["source_actor_id"],
                source["interval_seconds"],
                source["first_v4_slot"],
            )
            descriptor_identity = (
                descriptor.source_kind,
                descriptor.schedule_id,
                descriptor.publication_schedule_id,
                descriptor.workspace_id,
                descriptor.goal_id,
                descriptor.goal_revision,
                descriptor.source_actor_id,
                descriptor.interval_seconds,
                _aware(descriptor.first_v4_slot, "first V4 slot"),
            )
            if source_identity != descriptor_identity:
                raise PermissionError("remote and canonical source identities do not match")
            if self.workspace_id != descriptor.workspace_id:
                raise PermissionError("bound schedule workspace is outside controller scope")
            source_values: dict[str, object] = {
                "source_kind": source["source_kind"],
                "schedule_id": str(source["schedule_id"]),
                "publication_schedule_id": (
                    str(source["publication_schedule_id"])
                    if source["publication_schedule_id"] is not None
                    else None
                ),
                "workspace_id": str(source["workspace_id"]),
                "goal_id": str(source["goal_id"]),
                "goal_revision": source["goal_revision"],
                "source_actor_id": str(source["source_actor_id"]),
                "source_owner_subject_id": str(descriptor.source_owner_subject_id),
                "schedule_operator_subject_id": str(descriptor.schedule_operator_subject_id),
                "exclusion_reason": None,
            }
            if descriptor.source_owner_subject_id == descriptor.schedule_operator_subject_id:
                raise ValueError("source owner and schedule operator identities must differ")
        else:
            if sources:
                raise PermissionError("current local source cannot be excluded from its remote member")
            source_values = {
                "source_kind": None,
                "schedule_id": None,
                "publication_schedule_id": None,
                "workspace_id": None,
                "goal_id": None,
                "goal_revision": None,
                "source_actor_id": None,
                "source_owner_subject_id": None,
                "schedule_operator_subject_id": None,
                "exclusion_reason": descriptor.exclusion_reason,
            }
        ordered_actions = sorted(
            all_actions,
            key=lambda item: (
                _aware(item.scheduled_slot, "scheduled slot"),
                item.action_id_sha256,
            ),
        )
        history_action_set = _ProductionScheduleHashes.jsonb(
            connection, [item.action_id_sha256 for item in ordered_actions]
        )
        history_page_chain = _ProductionScheduleHashes.jsonb(
            connection, [page["page_receipt_sha256"] for page in history_pages]
        )
        watermark_action = ordered_actions[-1] if ordered_actions else None
        history_watermark = (
            _aware(watermark_action.scheduled_slot, "history watermark")
            if watermark_action
            else None
        )
        history_watermark_sha = (
            watermark_action.action_id_sha256 if watermark_action else None
        )
        snapshot = {
            "schema_version": _MEMBER_SCHEMA,
            "target_alias": self.target_alias,
            "namespace": self.namespace,
            "remote_schedule_id": descriptor.remote_schedule_id,
            "classification": descriptor.classification,
            **source_values,
            "owner_ref": descriptor.owner_ref,
            "workflow_type": descriptor.workflow_type,
            "action_id": descriptor.action_id,
            "argument_sha256": descriptor.argument_sha256,
            "task_queue": descriptor.task_queue,
            "route_id": str(descriptor.route_id),
            "route_tuple_sha256": descriptor.route_tuple_sha256,
            "slot_id": str(descriptor.slot_id),
            "owner_id": str(descriptor.owner_id),
            "fencing_epoch": descriptor.fencing_epoch,
            "route_manifest_sha256": descriptor.route_manifest_sha256,
            "artifact_lock_sha256": descriptor.artifact_lock_sha256,
            "cadence_policy_revision": descriptor.cadence_policy_revision,
            "interval_seconds": descriptor.interval_seconds,
            "first_v4_slot": _time_text(descriptor.first_v4_slot, "first V4 slot"),
            "observed_last_slot": (
                _time_text(source["observed_last_slot"], "observed last slot")
                if descriptor.classification == "bound"
                else None
            ),
            "remote_state": descriptor.remote_state,
            "next_action": _time_text(descriptor.next_action, "next action"),
            "running_action_count": descriptor.running_action_count,
            "history_page_count": len(history_pages),
            "history_action_count": len(all_actions),
            "history_page_chain_sha256": history_page_chain,
            "history_action_set_sha256": history_action_set,
            "history_watermark": _time_text(history_watermark, "history watermark"),
            "history_watermark_sha256": history_watermark_sha,
        }
        if frozenset(snapshot) != _MEMBER_KEYS:
            raise AssertionError("production schedule member encoder drifted from 0048")
        member = {
            "remote_schedule_id": descriptor.remote_schedule_id,
            "classification": descriptor.classification,
            "member_snapshot": snapshot,
            "member_sha256": _ProductionScheduleHashes.jsonb(connection, snapshot),
            "history_page_count": len(history_pages),
            "history_action_count": len(all_actions),
            "history_page_chain_sha256": history_page_chain,
            "history_action_set_sha256": history_action_set,
            "history_watermark": history_watermark,
            "history_watermark_sha256": history_watermark_sha,
        }
        return snapshot, member


    def _read_listing(self) -> list[tuple[TemporalScheduleInventoryPage, str | None]]:
        pages: list[tuple[TemporalScheduleInventoryPage, str | None]] = []
        seen_tokens: set[str] = set()
        token: str | None = None
        last_remote_id: str | None = None
        while True:
            self._page_limit(len(pages))
            page = self.target.list_page(token)
            if not isinstance(page, TemporalScheduleInventoryPage):
                raise ValueError("target returned an untyped inventory page")
            pages.append((page, token))
            for descriptor in page.items:
                if last_remote_id is not None and descriptor.remote_schedule_id <= last_remote_id:
                    raise ValueError("target inventory must be complete and strictly sorted")
                last_remote_id = descriptor.remote_schedule_id
            if page.next_page_token is None:
                break
            if page.next_page_token in seen_tokens or page.next_page_token == token:
                raise ValueError("target inventory cursor repeated before terminal page")
            seen_tokens.add(page.next_page_token)
            token = page.next_page_token
        return pages


    def _read_history(self, remote_schedule_id: str):
        pages: list[TemporalScheduleHistoryPage] = []
        seen_tokens: set[str] = set()
        token: str | None = None
        previous: tuple[datetime, str] | None = None
        actions: list[TemporalScheduleAction] = []
        while True:
            self._page_limit(len(pages))
            page = self.target.history_page(remote_schedule_id, token)
            if not isinstance(page, TemporalScheduleHistoryPage):
                raise ValueError("target returned an untyped history page")
            for action in page.items:
                current = (_aware(action.scheduled_slot, "scheduled slot"), action.action_id_sha256)
                if previous is not None and current < previous:
                    raise ValueError("target schedule history must be ordered by slot and action digest")
                previous = current
                actions.append(action)
            pages.append(page)
            if page.next_page_token is None:
                break
            if page.next_page_token in seen_tokens or page.next_page_token == token:
                raise ValueError("target history cursor repeated before terminal page")
            seen_tokens.add(page.next_page_token)
            token = page.next_page_token
        return pages, actions


    def capture_inventory(
        self,
        *,
        inventory_id: UUID,
        source_tool: str,
        source_tool_version: str,
        candidate_sha256: str,
        configuration_sha256: str,
    ) -> ProductionInventoryReceipt:
        if not isinstance(inventory_id, UUID):
            raise ValueError("production inventory UUID required")
        for label, value in (("source tool", source_tool), ("source tool version", source_tool_version)):
            _text(value, label)
        _digest(candidate_sha256, "candidate digest")
        _digest(configuration_sha256, "configuration digest")
        raw_listing_pages = self._read_listing()
        descriptors = [
            descriptor for page, _ in raw_listing_pages for descriptor in page.items
        ]
        history: dict[str, tuple[list[TemporalScheduleHistoryPage], list[TemporalScheduleAction]]] = {}
        for descriptor in descriptors:
            history[descriptor.remote_schedule_id] = self._read_history(descriptor.remote_schedule_id)
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='5s'")
            captured_at = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            with connection.transaction():
                listing_pages: list[dict[str, object]] = []
                member_rows: list[dict[str, object]] = []
                histories_to_insert: list[dict[str, object]] = []
                page_groups: dict[int, list[str]] = {}
                for page_index, (page, request_token) in enumerate(raw_listing_pages):
                    request_hash = self._cursor_hash(
                        connection,
                        request_token,
                        "salience.p1-production-schedule-inventory.start.v1",
                    )
                    next_hash = (
                        self._cursor_hash(connection, page.next_page_token, "")
                        if page.next_page_token is not None
                        else None
                    )
                    page_groups[page_index] = []
                    for descriptor in page.items:
                        history_pages, actions = history[descriptor.remote_schedule_id]
                        history_page_rows: list[dict[str, object]] = []
                        token_history: str | None = None
                        for history_index, history_page in enumerate(history_pages):
                            request_cursor = self._cursor_hash(
                                connection,
                                token_history,
                                "salience.p1-production-schedule-history.start.v1:" + descriptor.remote_schedule_id,
                            )
                            next_cursor = (
                                self._cursor_hash(connection, history_page.next_page_token, "")
                                if history_page.next_page_token is not None
                                else None
                            )
                            action_payloads = [action.payload() for action in history_page.items]
                            action_digests = [
                                action.action_id_sha256
                                for action in sorted(
                                    history_page.items,
                                    key=lambda item: (
                                        _aware(item.scheduled_slot, "scheduled slot"),
                                        item.action_id_sha256,
                                    ),
                                )
                            ]
                            page_item_set = _ProductionScheduleHashes.jsonb(
                                connection, action_digests
                            )
                            last_item = max(
                                history_page.items,
                                key=lambda action: (
                                    _aware(action.scheduled_slot, "scheduled slot"),
                                    action.action_id_sha256,
                                ),
                            ).action_id_sha256 if history_page.items else None
                            response_hash = _ProductionScheduleHashes.jsonb(
                                connection, action_payloads
                            )
                            page_receipt_data = {
                                "inventory_id": str(inventory_id),
                                "remote_schedule_id": descriptor.remote_schedule_id,
                                "page_index": history_index,
                                "request_cursor_sha256": request_cursor,
                                "response_sha256": response_hash,
                                "item_count": len(action_payloads),
                                "next_cursor_sha256": next_cursor,
                                "is_terminal": history_page.next_page_token is None,
                                "item_set_sha256": page_item_set,
                                "last_item_sha256": last_item,
                            }
                            page_receipt = _ProductionScheduleHashes.jsonb(
                                connection, page_receipt_data
                            )
                            history_page_rows.append(
                                {
                                    "inventory_id": inventory_id,
                                    "remote_schedule_id": descriptor.remote_schedule_id,
                                    "page_index": history_index,
                                    "request_cursor_sha256": request_cursor,
                                    "page_items": action_payloads,
                                    "response_sha256": response_hash,
                                    "item_count": len(action_payloads),
                                    "next_cursor_sha256": next_cursor,
                                    "is_terminal": history_page.next_page_token is None,
                                    "item_set_sha256": page_item_set,
                                    "last_item_sha256": last_item,
                                    "page_receipt_sha256": page_receipt,
                                }
                            )
                            if history_page.next_page_token is not None:
                                token_history = history_page.next_page_token
                        _, member = self._member_snapshot(
                            connection, descriptor, history_page_rows, actions
                        )
                        member_rows.append(member | {"page_index": page_index})
                        page_groups[page_index].append(descriptor.remote_schedule_id)
                        histories_to_insert.extend(history_page_rows)
                    listing_items = [
                        {
                            "remote_schedule_id": remote_id,
                            "member_sha256": next(
                                member["member_sha256"]
                                for member in member_rows
                                if member["remote_schedule_id"] == remote_id
                            ),
                        }
                        for remote_id in sorted(page_groups[page_index])
                    ]
                    response_hash = _ProductionScheduleHashes.jsonb(connection, listing_items)
                    item_set_hash = _ProductionScheduleHashes.jsonb(
                        connection,
                        [item["member_sha256"] for item in listing_items],
                    )
                    page_receipt_data = {
                        "inventory_id": str(inventory_id),
                        "remote_schedule_id": None,
                        "page_index": page_index,
                        "request_cursor_sha256": request_hash,
                        "response_sha256": response_hash,
                        "item_count": len(listing_items),
                        "next_cursor_sha256": next_hash,
                        "is_terminal": page.next_page_token is None,
                        "item_set_sha256": item_set_hash,
                        "last_item_sha256": None,
                    }
                    listing_pages.append(
                        {
                            "inventory_id": inventory_id,
                            "remote_schedule_id": None,
                            "page_index": page_index,
                            "request_cursor_sha256": request_hash,
                            "page_items": listing_items,
                            "response_sha256": response_hash,
                            "item_count": len(listing_items),
                            "next_cursor_sha256": next_hash,
                            "is_terminal": page.next_page_token is None,
                            "item_set_sha256": item_set_hash,
                            "last_item_sha256": None,
                            "page_receipt_sha256": _ProductionScheduleHashes.jsonb(
                                connection, page_receipt_data
                            ),
                        }
                    )
                inventory_page_chain = _ProductionScheduleHashes.jsonb(
                    connection,
                    [page["page_receipt_sha256"] for page in listing_pages],
                )
                member_set = [
                    {
                        "remote_schedule_id": member["remote_schedule_id"],
                        "member_sha256": member["member_sha256"],
                    }
                    for member in sorted(member_rows, key=lambda row: row["remote_schedule_id"])
                ]
                member_set_sha = _ProductionScheduleHashes.jsonb(connection, member_set)
                bound_count = sum(member["classification"] == "bound" for member in member_rows)
                out_of_scope_count = len(member_rows) - bound_count
                inventory_digest_data = {
                    "schema_version": _INVENTORY_SCHEMA,
                    "inventory_id": str(inventory_id),
                    "target_alias": self.target_alias,
                    "namespace": self.namespace,
                    "captured_at": json.loads(
                        connection.execute(
                            "SELECT to_jsonb(%s::timestamptz)::text AS canonical",
                            (captured_at,),
                        ).fetchone()["canonical"]
                    ),
                    "source_tool": source_tool,
                    "source_tool_version": source_tool_version,
                    "candidate_sha256": candidate_sha256,
                    "configuration_sha256": configuration_sha256,
                    "page_count": len(listing_pages),
                    "remote_member_count": len(member_rows),
                    "bound_member_count": bound_count,
                    "out_of_scope_member_count": out_of_scope_count,
                    "inventory_page_chain_sha256": inventory_page_chain,
                    "member_set_sha256": member_set_sha,
                }
                inventory_sha = _ProductionScheduleHashes.jsonb(connection, inventory_digest_data)
                connection.execute(
                    """INSERT INTO public.v4_production_schedule_inventories(
                         inventory_id,target_alias,namespace,captured_at,source_tool,
                         source_tool_version,candidate_sha256,configuration_sha256,
                         page_count,remote_member_count,bound_member_count,
                         out_of_scope_member_count,inventory_page_chain_sha256,
                         member_set_sha256,inventory_sha256)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        inventory_id,
                        self.target_alias,
                        self.namespace,
                        captured_at,
                        source_tool,
                        source_tool_version,
                        candidate_sha256,
                        configuration_sha256,
                        len(listing_pages),
                        len(member_rows),
                        bound_count,
                        out_of_scope_count,
                        inventory_page_chain,
                        member_set_sha,
                        inventory_sha,
                    ),
                )
                for member in member_rows:
                    connection.execute(
                        """INSERT INTO public.v4_production_schedule_inventory_members(
                             inventory_id,remote_schedule_id,page_index,classification,
                             member_snapshot,member_sha256,history_page_count,
                             history_action_count,history_page_chain_sha256,
                             history_action_set_sha256,history_watermark,
                             history_watermark_sha256)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            inventory_id,
                            member["remote_schedule_id"],
                            member["page_index"],
                            member["classification"],
                            Jsonb(member["member_snapshot"]),
                            member["member_sha256"],
                            member["history_page_count"],
                            member["history_action_count"],
                            member["history_page_chain_sha256"],
                            member["history_action_set_sha256"],
                            member["history_watermark"],
                            member["history_watermark_sha256"],
                        ),
                    )
                for page in listing_pages + histories_to_insert:
                    connection.execute(
                        """INSERT INTO public.v4_production_schedule_inventory_pages(
                             inventory_id,remote_schedule_id,page_index,request_cursor_sha256,
                             page_items,response_sha256,item_count,next_cursor_sha256,
                             is_terminal,item_set_sha256,last_item_sha256,page_receipt_sha256)
                           VALUES(%(inventory_id)s,%(remote_schedule_id)s,%(page_index)s,
                             %(request_cursor_sha256)s,%(page_items)s,%(response_sha256)s,
                             %(item_count)s,%(next_cursor_sha256)s,%(is_terminal)s,
                             %(item_set_sha256)s,%(last_item_sha256)s,%(page_receipt_sha256)s)""",
                        {
                            **page,
                            "page_items": Jsonb(page["page_items"]),
                        },
                    )
            return ProductionInventoryReceipt(
                inventory_id=inventory_id,
                inventory_sha256=inventory_sha,
                page_count=len(listing_pages),
                member_count=len(member_rows),
                bound_member_count=bound_count,
                out_of_scope_member_count=out_of_scope_count,
            )


    def _approval_for_member(self, connection, inventory, member):
        snapshot = member["member_snapshot"]
        matches = connection.execute(
            """SELECT approval.id,approval.workspace_id,approval.policy_version_id,
                      approval.effect_type,approval.status,approval.request_context,
                      approval.decided_by,approval.decided_at,
                      policy.document_hash,policy.status AS policy_status
               FROM public.approval_requests approval
               JOIN public.policy_versions policy
                 ON policy.id=approval.policy_version_id
                AND policy.workspace_id=approval.workspace_id
               WHERE approval.workspace_id=%s
                 AND approval.effect_type='production_schedule_binding'
                 AND approval.status='approved'
                 AND approval.decided_by IS NOT NULL
                 AND approval.decided_at IS NOT NULL
                 AND (approval.request_context->>'expires_at')::timestamptz > clock_timestamp()
                 AND approval.request_context->>'schema_version'=%s
                 AND approval.request_context->>'inventory_id'=%s
                 AND approval.request_context->>'inventory_sha256'=%s
                 AND approval.request_context->>'remote_schedule_id'=%s
               FOR SHARE OF approval,policy""",
            (
                snapshot["workspace_id"],
                _APPROVAL_SCHEMA,
                str(inventory["inventory_id"]),
                inventory["inventory_sha256"],
                snapshot["remote_schedule_id"],
            ),
        ).fetchall()
        expected = {
            "schema_version": _APPROVAL_SCHEMA,
            "target_alias": self.target_alias,
            "namespace": self.namespace,
            "inventory_id": str(inventory["inventory_id"]),
            "inventory_sha256": inventory["inventory_sha256"],
            "source_kind": snapshot["source_kind"],
            "schedule_id": snapshot["schedule_id"],
            "publication_schedule_id": snapshot["publication_schedule_id"],
            "workspace_id": snapshot["workspace_id"],
            "goal_id": snapshot["goal_id"],
            "goal_revision": snapshot["goal_revision"],
            "source_actor_id": snapshot["source_actor_id"],
            "source_owner_subject_id": snapshot["source_owner_subject_id"],
            "schedule_operator_subject_id": snapshot["schedule_operator_subject_id"],
            "remote_schedule_id": snapshot["remote_schedule_id"],
            "task_queue": snapshot["task_queue"],
            "workflow_type": snapshot["workflow_type"],
            "action_id": snapshot["action_id"],
            "argument_sha256": snapshot["argument_sha256"],
            "route_id": snapshot["route_id"],
            "route_tuple_sha256": snapshot["route_tuple_sha256"],
            "slot_id": snapshot["slot_id"],
            "owner_id": snapshot["owner_id"],
            "fencing_epoch": snapshot["fencing_epoch"],
            "route_manifest_sha256": snapshot["route_manifest_sha256"],
            "artifact_lock_sha256": snapshot["artifact_lock_sha256"],
            "cadence_policy_revision": snapshot["cadence_policy_revision"],
            "first_v4_slot": snapshot["first_v4_slot"],
            "history_watermark_sha256": snapshot["history_watermark_sha256"],
        }
        matches = [
            row
            for row in matches
            if isinstance(row["request_context"], Mapping)
            and frozenset(row["request_context"]) == frozenset(_APPROVAL_FIELDS)
            and all(row["request_context"].get(key) == value for key, value in expected.items())
        ]
        if len(matches) != 1:
            raise PermissionError("exact current typed production schedule approval required")
        approval = matches[0]
        context = approval["request_context"]
        if (
            str(approval["policy_version_id"]) != context["policy_version_id"]
            or approval["document_hash"] != context["policy_document_hash"]
            or approval["policy_status"] != "active"
        ):
            raise PermissionError("current approved production schedule policy required")
        return approval


    def finalize_inventory(self, inventory_id: UUID) -> ProductionInventoryReceipt:
        if not isinstance(inventory_id, UUID):
            raise ValueError("production inventory UUID required")
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='5s'")
            with connection.transaction():
                inventory = connection.execute(
                    """SELECT * FROM public.v4_production_schedule_inventories
                       WHERE inventory_id=%s AND target_alias=%s AND namespace=%s
                       FOR UPDATE""",
                    (inventory_id, self.target_alias, self.namespace),
                ).fetchone()
                if inventory is None:
                    raise PermissionError("scoped captured production inventory required")
                if inventory["finalized_at"] is not None:
                    return ProductionInventoryReceipt(
                        inventory_id=inventory_id,
                        inventory_sha256=inventory["inventory_sha256"],
                        page_count=inventory["page_count"],
                        member_count=inventory["remote_member_count"],
                        bound_member_count=inventory["bound_member_count"],
                        out_of_scope_member_count=inventory["out_of_scope_member_count"],
                        finalized_at=inventory["finalized_at"],
                    )
                members = connection.execute(
                    """SELECT * FROM public.v4_production_schedule_inventory_members
                       WHERE inventory_id=%s ORDER BY remote_schedule_id FOR UPDATE""",
                    (inventory_id,),
                ).fetchall()
                bound_members = [member for member in members if member["classification"] == "bound"]
                if len(bound_members) != inventory["bound_member_count"]:
                    raise ValueError("production inventory bound-member root is incomplete")
                for member in bound_members:
                    snapshot = member["member_snapshot"]
                    approval = self._approval_for_member(connection, inventory, member)
                    workspace_id = UUID(snapshot["workspace_id"])
                    goal_id = UUID(snapshot["goal_id"])
                    operator_id = UUID(snapshot["schedule_operator_subject_id"])
                    source_owner = connection.execute(
                        """SELECT enabled FROM public.identity_subjects
                           WHERE id=%s AND workspace_id=%s FOR SHARE""",
                        (snapshot["source_owner_subject_id"], workspace_id),
                    ).fetchone()
                    if source_owner is None or source_owner["enabled"] is not True:
                        raise PermissionError("current named source owner required")
                    self._require_current_schedule_authority(
                        connection,
                        workspace_id=workspace_id,
                        goal_id=goal_id,
                        operator_id=operator_id,
                        approval_id=approval["id"],
                        policy_version_id=approval["policy_version_id"],
                        policy_document_hash=approval["document_hash"],
                    )
                    self._route_decision(
                        route_id=UUID(snapshot["route_id"]),
                        route_tuple_sha256=snapshot["route_tuple_sha256"],
                        slot_id=UUID(snapshot["slot_id"]),
                        owner_id=UUID(snapshot["owner_id"]),
                        fencing_epoch=snapshot["fencing_epoch"],
                        route_manifest_sha256=snapshot["route_manifest_sha256"],
                        artifact_lock_sha256=snapshot["artifact_lock_sha256"],
                        operation_id=goal_id,
                        permit_id=approval["id"],
                        action_kind="schedule_bind",
                    )
                    connection.execute(
                        """INSERT INTO public.v4_production_schedule_source_bindings(
                             inventory_id,inventory_sha256,target_alias,namespace,
                             remote_schedule_id,member_sha256,source_kind,schedule_id,
                             publication_schedule_id,source_approval_id,source_actor_id,
                             source_owner_subject_id,schedule_operator_subject_id,workspace_id,
                             goal_id,goal_revision,owner_ref,task_queue,workflow_type,action_id,
                             argument_sha256,route_id,route_tuple_sha256,slot_id,owner_id,
                             fencing_epoch,route_manifest_sha256,artifact_lock_sha256,
                             cadence_policy_revision,interval_seconds,first_v4_slot,
                             observed_last_slot,remote_state,next_action,running_action_count,
                             history_page_count,history_action_count,history_page_chain_sha256,
                             history_action_set_sha256,history_watermark,
                             history_watermark_sha256,member_snapshot,binding_fingerprint)
                           VALUES(
                             %(inventory_id)s,%(inventory_sha256)s,%(target_alias)s,%(namespace)s,
                             %(remote_schedule_id)s,%(member_sha256)s,%(source_kind)s,%(schedule_id)s,
                             %(publication_schedule_id)s,%(source_approval_id)s,%(source_actor_id)s,
                             %(source_owner_subject_id)s,%(schedule_operator_subject_id)s,%(workspace_id)s,
                             %(goal_id)s,%(goal_revision)s,%(owner_ref)s,%(task_queue)s,%(workflow_type)s,
                             %(action_id)s,%(argument_sha256)s,%(route_id)s,%(route_tuple_sha256)s,
                             %(slot_id)s,%(owner_id)s,%(fencing_epoch)s,%(route_manifest_sha256)s,
                             %(artifact_lock_sha256)s,%(cadence_policy_revision)s,%(interval_seconds)s,
                             %(first_v4_slot)s,%(observed_last_slot)s,%(remote_state)s,%(next_action)s,
                             %(running_action_count)s,%(history_page_count)s,%(history_action_count)s,
                             %(history_page_chain_sha256)s,%(history_action_set_sha256)s,
                             %(history_watermark)s,%(history_watermark_sha256)s,%(member_snapshot)s,
                             %(binding_fingerprint)s)""",
                        {
                            "inventory_id": inventory_id,
                            "inventory_sha256": inventory["inventory_sha256"],
                            "target_alias": self.target_alias,
                            "namespace": self.namespace,
                            "remote_schedule_id": member["remote_schedule_id"],
                            "member_sha256": member["member_sha256"],
                            "source_kind": snapshot["source_kind"],
                            "schedule_id": snapshot["schedule_id"],
                            "publication_schedule_id": snapshot["publication_schedule_id"],
                            "source_approval_id": approval["id"],
                            "source_actor_id": snapshot["source_actor_id"],
                            "source_owner_subject_id": snapshot["source_owner_subject_id"],
                            "schedule_operator_subject_id": snapshot["schedule_operator_subject_id"],
                            "workspace_id": snapshot["workspace_id"],
                            "goal_id": snapshot["goal_id"],
                            "goal_revision": snapshot["goal_revision"],
                            "owner_ref": snapshot["owner_ref"],
                            "task_queue": snapshot["task_queue"],
                            "workflow_type": snapshot["workflow_type"],
                            "action_id": snapshot["action_id"],
                            "argument_sha256": snapshot["argument_sha256"],
                            "route_id": snapshot["route_id"],
                            "route_tuple_sha256": snapshot["route_tuple_sha256"],
                            "slot_id": snapshot["slot_id"],
                            "owner_id": snapshot["owner_id"],
                            "fencing_epoch": snapshot["fencing_epoch"],
                            "route_manifest_sha256": snapshot["route_manifest_sha256"],
                            "artifact_lock_sha256": snapshot["artifact_lock_sha256"],
                            "cadence_policy_revision": snapshot["cadence_policy_revision"],
                            "interval_seconds": snapshot["interval_seconds"],
                            "first_v4_slot": snapshot["first_v4_slot"],
                            "observed_last_slot": snapshot["observed_last_slot"],
                            "remote_state": snapshot["remote_state"],
                            "next_action": snapshot["next_action"],
                            "running_action_count": snapshot["running_action_count"],
                            "history_page_count": member["history_page_count"],
                            "history_action_count": member["history_action_count"],
                            "history_page_chain_sha256": member["history_page_chain_sha256"],
                            "history_action_set_sha256": member["history_action_set_sha256"],
                            "history_watermark": member["history_watermark"],
                            "history_watermark_sha256": member["history_watermark_sha256"],
                            "member_snapshot": Jsonb(snapshot),
                            "binding_fingerprint": "0" * 64,
                        },
                    )
                finalized_at = connection.execute(
                    "SELECT public.v4_finalize_production_schedule_inventory_v1(%s) AS finalized_at",
                    (inventory_id,),
                ).fetchone()["finalized_at"]
                final_row = connection.execute(
                    "SELECT * FROM public.v4_production_schedule_inventories WHERE inventory_id=%s",
                    (inventory_id,),
                ).fetchone()
                return ProductionInventoryReceipt(
                    inventory_id=inventory_id,
                    inventory_sha256=final_row["inventory_sha256"],
                    page_count=final_row["page_count"],
                    member_count=final_row["remote_member_count"],
                    bound_member_count=final_row["bound_member_count"],
                    out_of_scope_member_count=final_row["out_of_scope_member_count"],
                    finalized_at=finalized_at,
                )


    def _binding_row(self, connection, *, schedule_id: UUID | None = None, remote_schedule_id: str | None = None):
        if (schedule_id is None) == (remote_schedule_id is None):
            raise ValueError("select one exact local or remote schedule identity")
        predicate = "binding.schedule_id=%s" if schedule_id is not None else "binding.remote_schedule_id=%s"
        value = schedule_id if schedule_id is not None else remote_schedule_id
        return connection.execute(
            """SELECT binding.*,inventory.finalized_at,
                      inventory.inventory_sha256 AS current_inventory_sha256,
                      public.v4_production_schedule_jsonb_sha256_v1(
                        binding.member_snapshot) AS current_member_sha256,
                      public.v4_production_schedule_jsonb_sha256_v1(
                        to_jsonb(binding)-'binding_fingerprint') AS current_binding_fingerprint,
                      approval.policy_version_id,approval.request_context,
                      policy.document_hash AS policy_document_hash
               FROM public.v4_production_schedule_source_bindings binding
               JOIN public.v4_production_schedule_inventories inventory
                 ON inventory.inventory_id=binding.inventory_id
               JOIN public.approval_requests approval
                 ON approval.id=binding.source_approval_id
               JOIN public.policy_versions policy
                 ON policy.id=approval.policy_version_id
               WHERE """ + predicate + """ AND binding.workspace_id=%s
                 AND binding.target_alias=%s AND binding.namespace=%s""",
            (value, self.workspace_id, self.target_alias, self.namespace),
        ).fetchone()


    @staticmethod
    def _binding_value(row) -> ProductionScheduleBinding:
        if row is None or row["finalized_at"] is None:
            raise PermissionError("exact finalized production source binding required")
        if row["current_inventory_sha256"] != row["inventory_sha256"]:
            raise PermissionError("current complete inventory digest required")
        snapshot = row["member_snapshot"]
        if (
            not isinstance(snapshot, Mapping)
            or frozenset(snapshot) != _MEMBER_KEYS
            or snapshot["remote_schedule_id"] != row["remote_schedule_id"]
            or row["current_member_sha256"] != row["member_sha256"]
            or row["current_binding_fingerprint"] != row["binding_fingerprint"]
        ):
            raise PermissionError("immutable production source member identity required")
        return ProductionScheduleBinding(
            inventory_id=row["inventory_id"],
            inventory_sha256=row["inventory_sha256"],
            target_alias=row["target_alias"],
            namespace=row["namespace"],
            remote_schedule_id=row["remote_schedule_id"],
            source_approval_id=row["source_approval_id"],
            member_sha256=row["member_sha256"],
            binding_fingerprint=row["binding_fingerprint"],
            source_kind=row["source_kind"],
            schedule_id=row["schedule_id"],
            publication_schedule_id=row["publication_schedule_id"],
            workspace_id=row["workspace_id"],
            goal_id=row["goal_id"],
            goal_revision=row["goal_revision"],
            source_actor_id=row["source_actor_id"],
            source_owner_subject_id=row["source_owner_subject_id"],
            schedule_operator_subject_id=row["schedule_operator_subject_id"],
            route_id=row["route_id"],
            route_tuple_sha256=row["route_tuple_sha256"],
            slot_id=row["slot_id"],
            owner_id=row["owner_id"],
            fencing_epoch=row["fencing_epoch"],
            route_manifest_sha256=row["route_manifest_sha256"],
            artifact_lock_sha256=row["artifact_lock_sha256"],
            interval_seconds=row["interval_seconds"],
            first_v4_slot=row["first_v4_slot"],
            member_snapshot=snapshot,
        )


    def binding_for_schedule(self, schedule_id: UUID) -> ProductionScheduleBinding:
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            row = self._binding_row(connection, schedule_id=schedule_id)
            binding = self._binding_value(row)
            self._validate_binding_route(binding)
            return binding


    def binding_for_remote_schedule(self, remote_schedule_id: str) -> ProductionScheduleBinding:
        _text(remote_schedule_id, "remote schedule ID")
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            row = self._binding_row(connection, remote_schedule_id=remote_schedule_id)
            binding = self._binding_value(row)
            self._validate_binding_route(binding)
            return binding


    def _validate_binding_route(self, binding: ProductionScheduleBinding) -> None:
        if (
            binding.target_alias != self.target_alias
            or binding.namespace != self.namespace
            or binding.route_id != self.route.route_id
            or binding.route_tuple_sha256 != self.route.route_tuple_sha256
            or binding.slot_id != self.route_lease.slot_id
            or binding.owner_id != self.route_lease.owner_id
            or binding.fencing_epoch != self.route_lease.fencing_epoch
            or binding.route_manifest_sha256 != self.route_lease.route_manifest_sha256
            or binding.artifact_lock_sha256 != self.route_lease.artifact_lock_sha256
            or binding.member_snapshot["task_queue"] != self.route.route_tuple.task_queue
        ):
            raise PermissionError("finalized source binding does not match the current route")


    def assert_binding(self, binding: ProductionScheduleBinding, *, action_kind: str) -> None:
        if not isinstance(binding, ProductionScheduleBinding):
            raise ValueError("typed immutable production schedule binding required")
        if action_kind not in {
            "schedule_prepare",
            "schedule_activate",
            "schedule_poll",
            "schedule_rollback",
            "schedule_readback",
            "schedule_pause",
            "schedule_resume",
            "schedule_admit",
        }:
            raise ValueError("bounded production schedule action kind required")
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            row = self._binding_row(connection, schedule_id=binding.schedule_id)
            current = self._binding_value(row)
            if current != binding:
                raise PermissionError("production source binding changed after controller setup")
            self._validate_binding_route(current)
            self._require_current_schedule_authority(
                connection,
                workspace_id=current.workspace_id,
                goal_id=current.goal_id,
                operator_id=current.schedule_operator_subject_id,
                approval_id=current.source_approval_id,
                policy_version_id=UUID(str(row["policy_version_id"])),
                policy_document_hash=row["policy_document_hash"],
            )
            self._route_decision(
                route_id=current.route_id,
                route_tuple_sha256=current.route_tuple_sha256,
                slot_id=current.slot_id,
                owner_id=current.owner_id,
                fencing_epoch=current.fencing_epoch,
                route_manifest_sha256=current.route_manifest_sha256,
                artifact_lock_sha256=current.artifact_lock_sha256,
                operation_id=current.goal_id,
                permit_id=current.source_approval_id,
                action_kind=action_kind,
            )
