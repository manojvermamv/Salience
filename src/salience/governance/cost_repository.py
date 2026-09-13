"""Transactional canonical cost reservation and settlement for external effects."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

from salience.governance.costs import (
    BudgetExceeded,
    CostSettlementStatus,
    IdempotencyConflict,
    ReservationNotFound,
)


_MICROS_PER_UNIT = Decimal("1000000")


@dataclass(frozen=True)
class DurableCostReservation:
    reservation_id: str
    budget_id: str
    reservation_key: str
    estimated_micros: int
    reserved_micros: int
    status: CostSettlementStatus


@dataclass(frozen=True)
class CostSettlementOutcome:
    reservation_id: str
    status: CostSettlementStatus
    actual_micros: int | None


class CostReservationRepository:
    """Own durable monetary transitions; provider adapters never write these tables."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def reserve_for_effect(
        self,
        *,
        budget_id: str,
        reservation_key: str,
        job_id: str,
        external_effect_id: str,
        creative_job_id: str,
        estimated_micros: int,
    ) -> DurableCostReservation:
        if estimated_micros < 0:
            raise ValueError("estimated_micros must be non-negative")
        return await asyncio.to_thread(
            self._reserve_for_effect,
            budget_id,
            reservation_key,
            job_id,
            external_effect_id,
            creative_job_id,
            estimated_micros,
        )

    async def reserve_for_publication_effect(
        self,
        *,
        budget_id: str,
        reservation_key: str,
        job_id: str,
        external_effect_id: str,
        publication_plan_id: str,
        estimated_micros: int,
    ) -> DurableCostReservation:
        if estimated_micros < 0:
            raise ValueError("estimated_micros must be non-negative")
        return await asyncio.to_thread(
            self._reserve_for_publication_effect,
            budget_id,
            reservation_key,
            job_id,
            external_effect_id,
            publication_plan_id,
            estimated_micros,
        )

    async def record_actual_usage(
        self, reservation_id: str, actual_micros: int | None
    ) -> CostSettlementOutcome:
        if actual_micros is not None and actual_micros < 0:
            raise ValueError("actual_micros must be non-negative")
        if actual_micros is not None:
            return await self.settle(reservation_id, actual_micros=actual_micros)
        return await asyncio.to_thread(self._mark_pending_actual, reservation_id)

    async def settle(self, reservation_id: str, *, actual_micros: int) -> CostSettlementOutcome:
        if actual_micros < 0:
            raise ValueError("actual_micros must be non-negative")
        return await asyncio.to_thread(self._settle, reservation_id, actual_micros)

    async def release_unused(self, reservation_id: str) -> CostSettlementOutcome:
        return await asyncio.to_thread(self._release_unused, reservation_id)

    async def reservation_count(self, *, effect_id: str) -> int:
        return await asyncio.to_thread(self._reservation_count, effect_id)

    async def attach_provider_job(self, *, reservation_id: str, provider_job_id: str) -> None:
        await asyncio.to_thread(self._attach_provider_job, reservation_id, provider_job_id)

    def _reserve_for_effect(
        self,
        budget_id: str,
        reservation_key: str,
        job_id: str,
        external_effect_id: str,
        creative_job_id: str,
        estimated_micros: int,
    ) -> DurableCostReservation:
        with psycopg.connect(self._database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT limit_amount, status FROM budgets WHERE id = %s FOR UPDATE", (budget_id,)
            )
            budget = cursor.fetchone()
            if budget is None:
                raise KeyError(f"budget not found: {budget_id}")
            limit_amount, budget_status = budget
            if budget_status != "active":
                raise BudgetExceeded("budget is not active")
            cursor.execute(
                """
                SELECT id::text, estimated_amount, reserved_amount, status, job_id::text,
                       external_effect_id::text
                FROM budget_reservations
                WHERE budget_id = %s AND reservation_key = %s
                FOR UPDATE
                """,
                (budget_id, reservation_key),
            )
            existing = cursor.fetchone()
            if existing is not None:
                reservation_id, estimated, reserved, status, persisted_job, persisted_effect = existing
                if (
                    _to_micros(estimated) != estimated_micros
                    or persisted_job != job_id
                    or persisted_effect != external_effect_id
                ):
                    raise IdempotencyConflict("reservation key already has different cost inputs")
                return DurableCostReservation(
                    reservation_id,
                    budget_id,
                    reservation_key,
                    _to_micros(estimated),
                    _to_micros(reserved),
                    CostSettlementStatus(status),
                )

            self._verify_effect_ownership(cursor, creative_job_id, external_effect_id, job_id)
            available_micros = _to_micros(limit_amount) - self._committed_micros(cursor, budget_id)
            if estimated_micros > available_micros:
                raise BudgetExceeded("reservation would exceed the available budget")
            amount = _to_amount(estimated_micros)
            cursor.execute(
                """
                INSERT INTO budget_reservations (
                    budget_id, job_id, external_effect_id, reservation_key, estimated_amount,
                    reserved_amount, status
                ) VALUES (%s, %s, %s, %s, %s, %s, 'reserved')
                RETURNING id::text
                """,
                (budget_id, job_id, external_effect_id, reservation_key, amount, amount),
            )
            reservation_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO creative_job_effects (
                    creative_job_id, external_effect_id, budget_reservation_id, request_key, state,
                    trace_id, span_id
                )
                SELECT creative.id, %s, %s, %s, 'reserved', creative.trace_id, creative.span_id
                FROM creative_jobs creative
                WHERE creative.id = %s
                """,
                (external_effect_id, reservation_id, reservation_key, creative_job_id),
            )
            cursor.execute(
                "UPDATE creative_jobs SET budget_reservation_id = %s WHERE id = %s",
                (reservation_id, creative_job_id),
            )
            cursor.execute(
                """
                INSERT INTO cost_ledger_entries (
                    budget_reservation_id, job_id, estimated_amount, actual_amount, usage, recorded_at
                ) VALUES (%s, %s, %s, NULL, %s::jsonb, CURRENT_TIMESTAMP)
                """,
                (
                    reservation_id,
                    job_id,
                    amount,
                    _json({"kind": "estimated", "estimated_micros": estimated_micros}),
                ),
            )
            self._record_lineage(cursor, job_id, "cost.reserved", "allowed", {
                "reservation_id": reservation_id,
                "estimated_micros": estimated_micros,
            })
            return DurableCostReservation(
                reservation_id,
                budget_id,
                reservation_key,
                estimated_micros,
                estimated_micros,
                CostSettlementStatus.RESERVED,
            )

    def _mark_pending_actual(self, reservation_id: str) -> CostSettlementOutcome:
        with psycopg.connect(self._database_url) as connection, connection.cursor() as cursor:
            reservation = self._reservation_for_update(cursor, reservation_id)
            if reservation["status"] in {
                CostSettlementStatus.SETTLED.value,
                CostSettlementStatus.OVERAGE_PENDING_APPROVAL.value,
                CostSettlementStatus.RELEASED.value,
            }:
                return self._existing_outcome(cursor, reservation)
            cursor.execute(
                "UPDATE budget_reservations SET status = 'pending_actual' WHERE id = %s", (reservation_id,)
            )
            cursor.execute(
                "UPDATE creative_job_effects SET state = 'pending_actual' WHERE budget_reservation_id = %s",
                (reservation_id,),
            )
            cursor.execute(
                "UPDATE publication_plans SET state = 'pending_actual' WHERE budget_reservation_id = %s",
                (reservation_id,),
            )
            self._record_lineage(cursor, reservation["job_id"], "cost.pending_actual", "allowed", {
                "reservation_id": reservation_id,
            })
            return CostSettlementOutcome(
                reservation_id, CostSettlementStatus.PENDING_ACTUAL, None
            )

    def _settle(self, reservation_id: str, actual_micros: int) -> CostSettlementOutcome:
        with psycopg.connect(self._database_url) as connection, connection.cursor() as cursor:
            reservation = self._reservation_for_update(cursor, reservation_id)
            cursor.execute(
                """
                SELECT actual_amount, usage
                FROM cost_ledger_entries
                WHERE budget_reservation_id = %s AND usage ->> 'kind' = 'settlement'
                FOR UPDATE
                """,
                (reservation_id,),
            )
            settled = cursor.fetchone()
            if settled is not None:
                recorded_actual, usage = settled
                if _to_micros(recorded_actual) != actual_micros:
                    raise IdempotencyConflict("reservation already settled with another actual cost")
                return CostSettlementOutcome(
                    reservation_id,
                    CostSettlementStatus(usage["status"]),
                    actual_micros,
                )
            if reservation["status"] == CostSettlementStatus.RELEASED.value:
                raise IdempotencyConflict("released reservation cannot be settled")
            status = (
                CostSettlementStatus.OVERAGE_PENDING_APPROVAL
                if actual_micros > reservation["reserved_micros"]
                else CostSettlementStatus.SETTLED
            )
            cursor.execute(
                "UPDATE budget_reservations SET status = %s WHERE id = %s", (status.value, reservation_id)
            )
            cursor.execute(
                "UPDATE creative_job_effects SET state = %s WHERE budget_reservation_id = %s",
                (status.value, reservation_id),
            )
            cursor.execute(
                """
                UPDATE publication_plans
                SET actual_cost_micros = %s, state = %s, updated_at = CURRENT_TIMESTAMP
                WHERE budget_reservation_id = %s
                """,
                (actual_micros, status.value, reservation_id),
            )
            cursor.execute(
                """
                INSERT INTO cost_ledger_entries (
                    budget_reservation_id, job_id, estimated_amount, actual_amount, usage, recorded_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP)
                """,
                (
                    reservation_id,
                    reservation["job_id"],
                    _to_amount(reservation["estimated_micros"]),
                    _to_amount(actual_micros),
                    _json({"kind": "settlement", "status": status.value, "actual_micros": actual_micros}),
                ),
            )
            self._record_lineage(cursor, reservation["job_id"], "cost.settled", "denied" if status == CostSettlementStatus.OVERAGE_PENDING_APPROVAL else "allowed", {
                "reservation_id": reservation_id,
                "actual_micros": actual_micros,
                "status": status.value,
            })
            return CostSettlementOutcome(reservation_id, status, actual_micros)

    def _release_unused(self, reservation_id: str) -> CostSettlementOutcome:
        with psycopg.connect(self._database_url) as connection, connection.cursor() as cursor:
            reservation = self._reservation_for_update(cursor, reservation_id)
            if reservation["status"] == CostSettlementStatus.RELEASED.value:
                return CostSettlementOutcome(reservation_id, CostSettlementStatus.RELEASED, None)
            if reservation["status"] in {
                CostSettlementStatus.SETTLED.value,
                CostSettlementStatus.OVERAGE_PENDING_APPROVAL.value,
            }:
                return self._existing_outcome(cursor, reservation)
            cursor.execute("UPDATE budget_reservations SET status = 'released' WHERE id = %s", (reservation_id,))
            cursor.execute(
                """
                INSERT INTO cost_ledger_entries (
                    budget_reservation_id, job_id, estimated_amount, actual_amount, usage, recorded_at
                ) VALUES (%s, %s, %s, NULL, %s::jsonb, CURRENT_TIMESTAMP)
                """,
                (
                    reservation_id,
                    reservation["job_id"],
                    _to_amount(reservation["estimated_micros"]),
                    _json({"kind": "released", "released_micros": reservation["reserved_micros"]}),
                ),
            )
            self._record_lineage(cursor, reservation["job_id"], "cost.released", "allowed", {
                "reservation_id": reservation_id,
            })
            return CostSettlementOutcome(reservation_id, CostSettlementStatus.RELEASED, None)

    def _reservation_count(self, effect_id: str) -> int:
        with psycopg.connect(self._database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM budget_reservations WHERE external_effect_id = %s", (effect_id,)
            )
            return int(cursor.fetchone()[0])

    def _reserve_for_publication_effect(
        self,
        budget_id: str,
        reservation_key: str,
        job_id: str,
        external_effect_id: str,
        publication_plan_id: str,
        estimated_micros: int,
    ) -> DurableCostReservation:
        with psycopg.connect(self._database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT limit_amount, status FROM budgets WHERE id = %s FOR UPDATE", (budget_id,)
            )
            budget = cursor.fetchone()
            if budget is None:
                raise KeyError(f"budget not found: {budget_id}")
            limit_amount, budget_status = budget
            if budget_status != "active":
                raise BudgetExceeded("budget is not active")
            cursor.execute(
                """
                SELECT id::text, estimated_amount, reserved_amount, status, job_id::text,
                       external_effect_id::text
                FROM budget_reservations
                WHERE budget_id = %s AND reservation_key = %s
                FOR UPDATE
                """,
                (budget_id, reservation_key),
            )
            existing = cursor.fetchone()
            if existing is not None:
                reservation_id, estimated, reserved, status, persisted_job, persisted_effect = existing
                if (
                    _to_micros(estimated) != estimated_micros
                    or persisted_job != job_id
                    or persisted_effect != external_effect_id
                ):
                    raise IdempotencyConflict("reservation key already has different cost inputs")
                return DurableCostReservation(
                    reservation_id,
                    budget_id,
                    reservation_key,
                    _to_micros(estimated),
                    _to_micros(reserved),
                    CostSettlementStatus(status),
                )
            self._verify_publication_effect_ownership(
                cursor, publication_plan_id, external_effect_id, job_id, budget_id
            )
            available_micros = _to_micros(limit_amount) - self._committed_micros(cursor, budget_id)
            if estimated_micros > available_micros:
                raise BudgetExceeded("reservation would exceed the available budget")
            amount = _to_amount(estimated_micros)
            cursor.execute(
                """
                INSERT INTO budget_reservations (
                    budget_id, job_id, external_effect_id, reservation_key, estimated_amount,
                    reserved_amount, status
                ) VALUES (%s, %s, %s, %s, %s, %s, 'reserved')
                RETURNING id::text
                """,
                (budget_id, job_id, external_effect_id, reservation_key, amount, amount),
            )
            reservation_id = cursor.fetchone()[0]
            cursor.execute(
                """
                UPDATE publication_plans
                SET budget_reservation_id = %s, state = 'reserved', updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND (budget_reservation_id IS NULL OR budget_reservation_id = %s)
                """,
                (reservation_id, publication_plan_id, reservation_id),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflict("publication plan cannot be linked to this budget reservation")
            cursor.execute(
                """
                INSERT INTO cost_ledger_entries (
                    budget_reservation_id, job_id, estimated_amount, actual_amount, usage, recorded_at
                ) VALUES (%s, %s, %s, NULL, %s::jsonb, CURRENT_TIMESTAMP)
                """,
                (
                    reservation_id,
                    job_id,
                    amount,
                    _json({"kind": "estimated", "estimated_micros": estimated_micros}),
                ),
            )
            self._record_lineage(
                cursor,
                job_id,
                "cost.reserved",
                "allowed",
                {"reservation_id": reservation_id, "estimated_micros": estimated_micros},
            )
            return DurableCostReservation(
                reservation_id,
                budget_id,
                reservation_key,
                estimated_micros,
                estimated_micros,
                CostSettlementStatus.RESERVED,
            )

    def _attach_provider_job(self, reservation_id: str, provider_job_id: str) -> None:
        with psycopg.connect(self._database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE creative_job_effects
                SET provider_job_id = COALESCE(provider_job_id, %s), state = 'submitted',
                    updated_at = CURRENT_TIMESTAMP
                WHERE budget_reservation_id = %s
                  AND (provider_job_id IS NULL OR provider_job_id = %s)
                RETURNING creative_job_id::text
                """,
                (provider_job_id, reservation_id, provider_job_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("provider job cannot be linked to this budget reservation")

    @staticmethod
    def _committed_micros(cursor: psycopg.Cursor[Any], budget_id: str) -> int:
        cursor.execute(
            """
            SELECT COALESCE(sum(reserved_amount), 0)
            FROM budget_reservations
            WHERE budget_id = %s
              AND status IN ('reserved', 'pending_actual', 'overage_pending_approval')
            """,
            (budget_id,),
        )
        reserved = _to_micros(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT COALESCE(sum(entry.actual_amount), 0)
            FROM cost_ledger_entries entry
            JOIN budget_reservations reservation ON reservation.id = entry.budget_reservation_id
            WHERE reservation.budget_id = %s AND entry.usage ->> 'kind' = 'settlement'
            """,
            (budget_id,),
        )
        return reserved + _to_micros(cursor.fetchone()[0])

    @staticmethod
    def _verify_effect_ownership(
        cursor: psycopg.Cursor[Any], creative_job_id: str, external_effect_id: str, job_id: str
    ) -> None:
        cursor.execute(
            """
            SELECT creative.job_id::text, effect.job_id::text
            FROM creative_jobs creative
            JOIN external_effects effect ON effect.id = %s
            WHERE creative.id = %s
            """,
            (external_effect_id, creative_job_id),
        )
        ownership = cursor.fetchone()
        if ownership != (job_id, job_id):
            raise ValueError("creative job and external effect must belong to the same canonical job")

    @staticmethod
    def _verify_publication_effect_ownership(
        cursor: psycopg.Cursor[Any],
        publication_plan_id: str,
        external_effect_id: str,
        job_id: str,
        budget_id: str,
    ) -> None:
        cursor.execute(
            """
            SELECT effect.job_id::text, plan.external_effect_id::text, budget.workspace_id::text,
                   request.workspace_id::text, budget.content_program_id::text,
                   request.content_program_id::text
            FROM publication_plans plan
            JOIN publication_requests request ON request.id = plan.publication_request_id
            JOIN external_effects effect ON effect.id = %s
            JOIN budgets budget ON budget.id = %s
            WHERE plan.id = %s
            """,
            (external_effect_id, budget_id, publication_plan_id),
        )
        ownership = cursor.fetchone()
        if ownership is None:
            raise ValueError("publication plan, effect, and budget must exist")
        effect_job, plan_effect, budget_workspace, request_workspace, budget_program, request_program = ownership
        if (
            effect_job != job_id
            or plan_effect != external_effect_id
            or budget_workspace != request_workspace
            or budget_program not in {None, request_program}
        ):
            raise ValueError("publication plan, external effect, and budget must share canonical scope")

    @staticmethod
    def _reservation_for_update(cursor: psycopg.Cursor[Any], reservation_id: str) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT id::text, job_id::text, estimated_amount, reserved_amount, status
            FROM budget_reservations WHERE id = %s FOR UPDATE
            """,
            (reservation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ReservationNotFound(reservation_id)
        persisted_reservation_id, job_id, estimated, reserved, status = row
        return {
            "reservation_id": persisted_reservation_id,
            "job_id": job_id,
            "estimated_micros": _to_micros(estimated),
            "reserved_micros": _to_micros(reserved),
            "status": status,
        }

    @staticmethod
    def _existing_outcome(cursor: psycopg.Cursor[Any], reservation: dict[str, Any]) -> CostSettlementOutcome:
        cursor.execute(
            """
            SELECT budget_reservation_id::text, actual_amount
            FROM cost_ledger_entries
            WHERE budget_reservation_id = %s AND usage ->> 'kind' = 'settlement'
            """,
            (reservation["reservation_id"],),
        )
        row = cursor.fetchone()
        actual_micros = _to_micros(row[1]) if row is not None and row[1] is not None else None
        return CostSettlementOutcome(
            reservation["reservation_id"], CostSettlementStatus(reservation["status"]), actual_micros
        )

    @staticmethod
    def _record_lineage(
        cursor: psycopg.Cursor[Any], job_id: str, action: str, outcome: str, details: dict[str, object]
    ) -> None:
        cursor.execute(
            """
            SELECT workspace_id, workflow_run_id, trace_id, span_id
            FROM jobs WHERE id = %s FOR UPDATE
            """,
            (job_id,),
        )
        workspace_id, workflow_run_id, trace_id, span_id = cursor.fetchone()
        cursor.execute(
            "SELECT COALESCE(max(sequence_no), 0) + 1 FROM audit_events WHERE run_id = %s",
            (workflow_run_id,),
        )
        sequence_no = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO audit_events (
                workspace_id, job_id, run_id, sequence_no, actor_kind, action, resource_type,
                resource_id, outcome, trace_id, span_id, details
            ) VALUES (%s, %s, %s, %s, 'runtime', %s, 'budget_reservation', %s, %s, %s, %s, %s::jsonb)
            """,
            (workspace_id, job_id, workflow_run_id, sequence_no, action, details["reservation_id"], outcome, trace_id, span_id, _json(details)),
        )
        cursor.execute(
            """
            INSERT INTO provenance_records (
                workspace_id, job_id, origin_type, source_uri, source_hash, verification_status,
                c2pa_manifest, lineage, trace_id, span_id
            ) VALUES (%s, %s, 'cost_reservation', %s, %s, 'verified', '{}'::jsonb, %s::jsonb, %s, %s)
            """,
            (workspace_id, job_id, f"salience://cost-reservations/{details['reservation_id']}", details["reservation_id"], _json(details), trace_id, span_id),
        )


def _to_amount(micros: int) -> Decimal:
    return Decimal(micros) / _MICROS_PER_UNIT


def _to_micros(amount: Decimal | int) -> int:
    return int(Decimal(amount) * _MICROS_PER_UNIT)


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True)
