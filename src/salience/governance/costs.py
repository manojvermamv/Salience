from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4


class BudgetExceeded(ValueError):
    pass


class ReservationNotFound(KeyError):
    pass


class IdempotencyConflict(ValueError):
    pass


class CostKind(StrEnum):
    ESTIMATED = "estimated"
    RESERVED = "reserved"
    RELEASED = "released"
    ACTUAL = "actual"


class CostSettlementStatus(StrEnum):
    RESERVED = "reserved"
    PENDING_ACTUAL = "pending_actual"
    SETTLED = "settled"
    RELEASED = "released"
    OVERAGE_PENDING_APPROVAL = "overage_pending_approval"


@dataclass(frozen=True)
class BudgetReservation:
    id: UUID
    run_id: str
    estimated_micros: int
    reserved_micros: int
    status: str


@dataclass(frozen=True)
class CostRecord:
    id: UUID
    reservation_id: UUID
    kind: CostKind
    micros: int


@dataclass(frozen=True)
class CostLedgerEntry:
    id: UUID
    reservation_id: UUID
    actual_micros: int


class BudgetService:
    def __init__(self, *, limit_micros: int) -> None:
        if limit_micros < 0:
            raise ValueError("limit_micros must be non-negative")
        self.limit_micros = limit_micros
        self._reservations_by_id: dict[UUID, BudgetReservation] = {}
        self._reservations_by_run: dict[str, BudgetReservation] = {}
        self._settlements: dict[UUID, CostLedgerEntry] = {}
        self.ledger: list[CostRecord] = []

    @property
    def active_reserved_micros(self) -> int:
        return sum(
            reservation.reserved_micros
            for reservation in self._reservations_by_id.values()
            if reservation.status == "reserved"
        )

    @property
    def actual_micros(self) -> int:
        return sum(settlement.actual_micros for settlement in self._settlements.values())

    @property
    def available_micros(self) -> int:
        return self.limit_micros - self.active_reserved_micros - self.actual_micros

    def reserve(self, run_id: str, estimated_micros: int) -> BudgetReservation:
        if estimated_micros < 0:
            raise ValueError("estimated_micros must be non-negative")
        existing = self._reservations_by_run.get(run_id)
        if existing is not None:
            if existing.estimated_micros != estimated_micros:
                raise IdempotencyConflict("run already has a reservation with another estimate")
            return existing
        if estimated_micros > self.available_micros:
            raise BudgetExceeded("reservation would exceed the available budget")

        reservation = BudgetReservation(
            id=uuid4(),
            run_id=run_id,
            estimated_micros=estimated_micros,
            reserved_micros=estimated_micros,
            status="reserved",
        )
        self._reservations_by_id[reservation.id] = reservation
        self._reservations_by_run[run_id] = reservation
        self.ledger.extend(
            (
                CostRecord(uuid4(), reservation.id, CostKind.ESTIMATED, estimated_micros),
                CostRecord(uuid4(), reservation.id, CostKind.RESERVED, estimated_micros),
            )
        )
        return reservation

    def settle(self, reservation_id: UUID, actual_micros: int) -> CostLedgerEntry:
        if actual_micros < 0:
            raise ValueError("actual_micros must be non-negative")
        existing = self._settlements.get(reservation_id)
        if existing is not None:
            if existing.actual_micros != actual_micros:
                raise IdempotencyConflict("reservation already settled with another actual cost")
            return existing
        reservation = self._reservations_by_id.get(reservation_id)
        if reservation is None:
            raise ReservationNotFound(reservation_id)

        settled = BudgetReservation(
            id=reservation.id,
            run_id=reservation.run_id,
            estimated_micros=reservation.estimated_micros,
            reserved_micros=reservation.reserved_micros,
            status="settled",
        )
        self._reservations_by_id[reservation_id] = settled
        entry = CostLedgerEntry(uuid4(), reservation_id, actual_micros)
        self._settlements[reservation_id] = entry
        self.ledger.extend(
            (
                CostRecord(uuid4(), reservation_id, CostKind.RELEASED, reservation.reserved_micros),
                CostRecord(uuid4(), reservation_id, CostKind.ACTUAL, actual_micros),
            )
        )
        return entry
