"""Read the historical fixture liabilities through their original v1 authority."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.rows import dict_row


def read_fixture_liabilities(connection, workspace_id: UUID, inventory: dict[str, Any]):
    """Discover and revalidate every in-scope historical fixture hold.

    Task 9 keeps the fixture-v1 SQL and validation authority in
    ``publication_recovery``. The workspace inventory supplies the discovered
    set; this function independently re-queries it in the same read-only
    repeatable-read transaction and proves that no stored row was omitted.
    """
    from salience.cycles import publication_recovery

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            f"""SELECT {publication_recovery._HOLD_COLUMNS_V1}
                FROM public.v4_legacy_publication_recovery_holds
                WHERE workspace_id=%s
                ORDER BY reason, operation_id, id""",
            (workspace_id,),
        )
        stored_rows = cursor.fetchall()

    inventory_rows = inventory["fixture_holds"]
    inventory_ids = {
        (str(row["id"]), str(row["operation_id"]), row["reason"])
        for row in inventory_rows
    }
    stored_ids = {
        (str(row["id"]), str(row["operation_id"]), row["reason"])
        for row in stored_rows
    }
    if inventory_ids != stored_ids or len(inventory_rows) != len(stored_rows):
        raise ValueError("fixture inventory omitted or duplicated a workspace liability")

    liabilities = []
    for stored in stored_rows:
        publication_recovery._validate_v1_evidence(
            stored,
            computed_fingerprint=stored["computed_liability_fingerprint_v1"],
        )
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                publication_recovery._CANDIDATE_SQL,
                (
                    stored["reason"],
                    stored["operation_id"],
                    workspace_id,
                    stored["reason"],
                    stored["reason"],
                ),
            )
            current = cursor.fetchone()
        if current is None:
            raise ValueError("fixture publication liability changed after its stored snapshot")
        publication_recovery._validate_v1_evidence(
            current,
            computed_fingerprint=current["liability_fingerprint_v1"],
        )
        if not publication_recovery._same_evidence(stored, current):
            raise ValueError("fixture publication liability does not match its stored snapshot")
        liabilities.append(
            {
                "kind": "fixture-v1",
                "hold_id": stored["id"],
                "operation_id": stored["operation_id"],
                "reason": stored["reason"],
                "snapshot_version": stored["liability_snapshot_version"],
                "snapshot_fingerprint": stored["liability_fingerprint_v1"],
                "stored_snapshot": stored["liability_snapshot_v1"],
                "current_snapshot": current["liability_snapshot_v1"],
                "current_fingerprint": current["liability_fingerprint_v1"],
            }
        )
    return tuple(liabilities)
