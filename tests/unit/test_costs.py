import pytest

from salience.governance.costs import BudgetExceeded, CostKind, BudgetService


def test_budget_settlement_releases_reservation_and_is_idempotent() -> None:
    governance = BudgetService(limit_micros=100)

    reservation = governance.reserve("run-1", 50)
    entry = governance.settle(reservation.id, 30)

    assert entry.actual_micros == 30
    assert governance.active_reserved_micros == 0
    assert governance.actual_micros == 30
    assert governance.settle(reservation.id, 30).id == entry.id
    assert [record.kind for record in governance.ledger] == [
        CostKind.ESTIMATED,
        CostKind.RESERVED,
        CostKind.RELEASED,
        CostKind.ACTUAL,
    ]


def test_budget_reservation_is_idempotent_and_refuses_overflow() -> None:
    governance = BudgetService(limit_micros=100)

    first = governance.reserve("run-1", 70)
    assert governance.reserve("run-1", 70).id == first.id
    with pytest.raises(BudgetExceeded):
        governance.reserve("run-2", 31)

