from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ._canonical import _evidence_ref_error, _iter_evidence_refs, sha256_json
from ._contracts import ROW_GATES, VALID_STATUSES, _ValidationState
from ._procedure_checks import REQUIRED_ROW_CHECK_IDS
from ._reconciliation import P0_EXTERNAL_PROOF_ROWS
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
from ._scope_binding import _execution_records, _target_manifest, _target_scope_identity


def _check_proofs(checks: Sequence[Mapping[str, Any]], required: set[str]) -> list[tuple[str, str]]:
    return [
        (check["check_id"], check["raw_result"]["sha256"])
        for check in checks
        if isinstance(check, Mapping) and isinstance(check.get("check_id"), str)
        and check["check_id"] in required
        and _evidence_ref_error(check.get("raw_result")) is None
    ]


def _validate_required_evidence_reuse(payload: Mapping[str, Any], state: _ValidationState) -> None:
    if payload.get("schema_version") != "1.1":
        return
    p0_proofs: dict[str, set[tuple[str, str]]] = {}
    row_proofs: dict[str, dict[tuple[str, ...], str]] = {}
    for path, execution in _execution_records(payload):
        if execution.get("status") != "PASS":
            continue
        row_id = execution.get("row_id")
        if not isinstance(row_id, str) or row_id not in REQUIRED_ROW_CHECK_IDS:
            continue
        checks = execution.get("normalized_checks", [])
        required_p0 = {proof for proof, rows in P0_EXTERNAL_PROOF_ROWS.items() if row_id in rows}
        for proof, digest in _check_proofs(checks, required_p0):
            p0_proofs.setdefault(digest, set()).add((proof, row_id))
        for _, digest in _check_proofs(checks, set(REQUIRED_ROW_CHECK_IDS[row_id])):
            row_proofs.setdefault(digest, {})[path] = row_id
    for uses in p0_proofs.values():
        if len({proof for proof, _ in uses}) > 1:
            for _, row_id in uses:
                state.add_row(row_id, "REQUIRED_P0_PROOF_REUSED",
                              "Different named P0 proofs must use distinct evidence across the package.")
    for digest, uses in row_proofs.items():
        shared_p0 = p0_proofs.get(digest, set())
        if len(uses) > 1 or shared_p0:
            for row_id in set(uses.values()) | {row_id for _, row_id in shared_p0}:
                state.add_row(row_id, "REQUIRED_ROW_CHECK_EVIDENCE_REUSED",
                              "Required row checks cannot reuse another execution's evidence or a P0 proof.")


def _validate_required_checks(
    row_id: str, checks: Sequence[Mapping[str, Any]], state: _ValidationState
) -> None:
    required_p0 = {proof for proof, rows in P0_EXTERNAL_PROOF_ROWS.items() if row_id in rows}
    p0_proofs = _check_proofs(checks, required_p0)
    p0_ids = {proof_id for proof_id, _ in p0_proofs}
    p0_digests = {digest for _, digest in p0_proofs}
    if missing := sorted(required_p0 - p0_ids):
        state.add_row(row_id, "REQUIRED_P0_PROOF_MISSING",
                      f"Row {row_id} is missing required proofs: {', '.join(missing)}.")
    if len(p0_proofs) != len(p0_ids) or len(p0_proofs) != len(p0_digests):
        state.add_row(row_id, "REQUIRED_P0_PROOF_REUSED",
                      f"Row {row_id} requires one distinct evidence record per named P0 proof.")

    required_row = set(REQUIRED_ROW_CHECK_IDS[row_id])
    row_check_proofs = _check_proofs(checks, required_row)
    check_ids = {check_id for check_id, _ in row_check_proofs}
    check_digests = {digest for _, digest in row_check_proofs}
    claimed_checks = [
        check["check_id"] for check in checks
        if isinstance(check, Mapping) and isinstance(check.get("check_id"), str)
        and check["check_id"] in required_row
    ]
    if missing := sorted(required_row - check_ids):
        state.add_row(row_id, "REQUIRED_ROW_CHECK_MISSING",
                      f"Row {row_id} is missing required checks with immutable evidence: {', '.join(missing)}.")
    if len(claimed_checks) != len(set(claimed_checks)):
        state.add_row(row_id, "REQUIRED_ROW_CHECK_DUPLICATE",
                      f"Row {row_id} requires exactly one record per named check.")
    if len(row_check_proofs) != len(check_digests):
        state.add_row(row_id, "REQUIRED_ROW_CHECK_EVIDENCE_REUSED",
                      f"Row {row_id} requires distinct evidence for each required check.")


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
    if payload.get("schema_version") != "1.1":
        _validate_execution(row_id=row_id, row=row, payload=payload, manifest=manifest, now=now,
                            expected_dependencies=expected_dependencies, state=state)
        return
    targets = manifest.get("row_targets", {}).get(row_id, {})
    executions = [row, *row.get("child_checks", [])]
    target_ids = [item.get("target_id") for item in executions]
    if set(target_ids) != set(targets) or len(target_ids) != len(targets) or not targets:
        state.add_row(row_id, "ROW_TARGET_INVENTORY_MISMATCH",
                      "Every signed row target requires exactly one execution record.", structural=True)
    scopes = [sha256_json(_target_scope_identity(target)) for target in targets.values()]
    if len(set(scopes)) != len(scopes):
        state.add_row(row_id, "ROW_TARGET_INVENTORY_MISMATCH",
                      "Each target must identify a distinct account, resource or route scope.", structural=True)
    used_results: set[str] = set()
    parent_runbook = row.get("procedure", {})
    for item in executions:
        scoped_manifest = _target_manifest(manifest, row_id, item.get("target_id"))
        if item is not row:
            procedure = item.get("procedure", {})
            if (item.get("row_id") != row_id or item.get("child_checks") is not None
                    or item.get("record_sha256") != sha256_json({k: v for k, v in item.items() if k != "record_sha256"})
                    or (row.get("status") == "PASS" and item.get("status") != "PASS")):
                state.add_row(row_id, "CHILD_EXECUTION_INVALID",
                              "Every child must bind its row, digest and execution status.", structural=True)
            if item.get("status") in {"PASS", "FAIL"} and (
                procedure.get("runbook_version") != parent_runbook.get("runbook_version")
                or procedure.get("runbook", {}).get("sha256") != parent_runbook.get("runbook", {}).get("sha256")
            ):
                state.add_row(row_id, "CHILD_RUNBOOK_MISMATCH",
                              "Child execution must use the row's pinned runbook and version.", structural=True)
        observations = [item.get("raw_result"), item.get("readback")]
        observations.extend(check.get("raw_result") for check in item.get("normalized_checks", []))
        control = item.get("negative_control", {})
        observations.extend((control.get("raw_denial"), control.get("independent_effect_readback")))
        digests = {ref["sha256"] for ref in observations if isinstance(ref, Mapping) and "sha256" in ref}
        if digests & used_results:
            state.add_row(row_id, "CHILD_EVIDENCE_REUSED",
                          "A target cannot reuse another target's execution results or readbacks.", structural=True)
        used_results.update(digests)
        _validate_execution(row_id=row_id, row=item, payload=payload, manifest=scoped_manifest, now=now,
                            expected_dependencies=expected_dependencies, state=state)


def _validate_execution(
    *, row_id: str, row: Mapping[str, Any], payload: Mapping[str, Any],
    manifest: Mapping[str, Any], now: datetime, expected_dependencies: Sequence[str],
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
        if payload.get("schema_version") == "1.1":
            _validate_required_checks(row_id, checks, state)
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
