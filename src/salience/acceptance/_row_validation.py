from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ._canonical import _evidence_ref_error, _iter_evidence_refs
from ._contracts import ROW_GATES, VALID_STATUSES, _ValidationState
from ._row_checks import (
    _manifest_row_scope_errors,
    _validate_credentials,
    _validate_dependency_records,
    _validate_freshness,
    _validate_negative_control,
    _validate_observability,
    _validate_people,
    _validate_procedure,
    _validate_transition,
)


def _validate_row(
    *,
    row_id: str,
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    now: datetime,
    expected_dependencies: Sequence[str],
    state: _ValidationState,
) -> None:
    if row.get("run_id") != payload.get("run_id"):
        state.add_row(row_id, "RUN_ID_MISMATCH", "Row run ID differs from the package run ID.", structural=True)
    if row.get("gate") != ROW_GATES[row_id]:
        state.add_row(row_id, "GATE_STATUS_MISMATCH", "Row gate does not match the contract.", structural=True)
    if row.get("evidence_class") != "EXTERNAL_PRODUCTION_ACCEPTANCE":
        state.add_row(
            row_id,
            "EVIDENCE_CLASS_NOT_EXTERNAL",
            "Fixture, CI, formal approval, and historical evidence cannot satisfy external acceptance.",
        )
    if row.get("status") not in VALID_STATUSES:
        state.add_row(row_id, "STATUS_INVALID", "Row status is not in the allowed enum.")
    if row.get("scope_match") is not True:
        state.add_row(row_id, "SCOPE_MISMATCH", "Row does not declare an exact scope match.")
    execution_record = row.get("status") in {"PASS", "FAIL"}
    if row.get("status") == "PASS":
        checks = row.get("normalized_checks", [])
        if not checks or any(
            check.get("status") != "PASS" or check.get("scope_match") is not True
            for check in checks
            if isinstance(check, Mapping)
        ):
            state.add_row(
                row_id,
                "RESULT_STATUS_MISMATCH",
                "A PASS row requires passing normalized checks on exact scope.",
            )
    if row.get("status") == "FAIL":
        checks = row.get("normalized_checks", [])
        has_executed_failure = any(
            isinstance(check, Mapping)
            and check.get("status") == "FAIL"
            and check.get("scope_match") is True
            and _evidence_ref_error(check.get("raw_result")) is None
            for check in checks
        ) if isinstance(checks, Sequence) and not isinstance(checks, (str, bytes)) else False
        if not has_executed_failure:
            state.add_row(
                row_id,
                "FAIL_WITHOUT_FAILED_ASSERTION",
                "A FAIL row requires at least one executed failed normalized assertion on exact scope.",
                structural=True,
            )

    for code in _manifest_row_scope_errors(row, manifest):
        state.add_row(row_id, code, f"Row {row_id} is not bound to the exact manifest scope.")
    for code in _validate_people(row, now, manifest):
        state.add_row(row_id, code, f"Row {row_id} has missing or invalid human authority.")
    freshness_record = row.get("freshness")
    if isinstance(freshness_record, Mapping):
        freshness, freshness_blockers = _validate_freshness(row, now)
        state.row_states[row_id].freshness = freshness
        for code in freshness_blockers:
            state.add_row(row_id, code, f"Row {row_id} freshness is not approved and current.")
    else:
        state.row_states[row_id].freshness = "STALE"
    if execution_record:
        for code in _validate_procedure(row, manifest):
            state.add_row(row_id, code, f"Row {row_id} procedure/evidence is missing or unpinned.")
        transition_blockers, failure_status = _validate_transition(row, manifest)
        for code in transition_blockers:
            state.add_row(row_id, code, f"Row {row_id} transition or rollback evidence is not verified.")
        if failure_status:
            state.row_states[row_id].claimed_status = failure_status
        for code in _validate_negative_control(row, row_id):
            state.add_row(row_id, code, f"Row {row_id} negative control lacks denial or zero-effect proof.")
        for code in _validate_credentials(row):
            state.add_row(row_id, code, f"Row {row_id} restores or reuses a revoked credential.")
        for code in _validate_observability(row, str(payload.get("run_id", "")), manifest):
            state.add_row(row_id, code, f"Row {row_id} observability is missing, uncorrelated, or unsafe.")
        for code in _validate_dependency_records(row, expected_dependencies, str(payload.get("run_id", "")), manifest):
            state.add_row(row_id, code, f"Row {row_id} dependencies do not match exact current prerequisites.")
    elif row.get("dependencies") is not None or row.get("dependency_evidence") is not None:
        for code in _validate_dependency_records(row, expected_dependencies, str(payload.get("run_id", "")), manifest):
            state.add_row(row_id, code, f"Row {row_id} dependencies do not match exact current prerequisites.")
    for ref in _iter_evidence_refs(row):
        code = _evidence_ref_error(ref)
        if code:
            state.add_row(row_id, "EVIDENCE_REFERENCE_INVALID", f"Row {row_id}: {code}.")
            break

    operator_status = row.get("operator_declaration", {}).get("status")
    reviewer_status = row.get("reviewer_decision", {}).get("status")
    if execution_record and row.get("status") == "PASS" and (
        operator_status != "PASS" or reviewer_status != "PASS"
    ):
        state.add_row(
            row_id,
            "RESULT_STATUS_MISMATCH",
            "PASS requires separate operator declaration and independent reviewer decision.",
        )
