from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._audit_validation import (
    _validate_evidence_index,
    _validate_final_audit,
    _validate_invalidations,
)
from ._canonical import (
    _canonical_value,
    _evidence_ref_error,
    _is_pass,
    _is_sha256,
    _iter_evidence_refs,
    _package_digest_payload,
    _schema_errors,
    canonical_json_bytes,
    sha256_json,
    validate_payload_against_schema,
)
from ._contracts import (
    DEFAULT_SCHEMA_PATH,
    FALLBACK_DEPENDENCIES,
    P0_RELEASE_PATH,
    P1_QUALIFICATION_MATRIX_PATH,
    PASS_WORDS,
    PROJECT_ROOT,
    RG0_ROW_IDS,
    RG1_ROW_IDS,
    ROW_GATES,
    ROW_IDS,
    REQUIRED_NEGATIVE_CONTROL_ROW_IDS,
    SOURCE_SHA_RE,
    VALID_STATUSES,
    AcceptanceDisposition,
    RowDisposition,
    _LOCAL_BLOCKING_CODES,
    _RowState,
    _ValidationState,
)
from ._manifest_validation import _validate_manifest, _validate_record_digests
from ._reconciliation import (
    _apply_row_holds,
    _compute_dependency_map,
    _read_ledgers,
    _validate_ledgers,
    _load_matrix_safely,
)
from ._row_validation import _validate_row


def _schema_for_default() -> Any:
    return json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))


def _safe_message(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def _invalid_result(
    payload: Any,
    blockers: Sequence[tuple[str, str]],
) -> AcceptanceDisposition:
    normalized = tuple(
        {"code": code, "message": message}
        for code, message in sorted(set(blockers), key=lambda item: (item[0], item[1]))
    )
    run_id = payload.get("run_id") if isinstance(payload, Mapping) else None
    package_digest = payload.get("package_digest") if isinstance(payload, Mapping) else None
    return AcceptanceDisposition(
        run_id=run_id if isinstance(run_id, str) else None,
        structural_status="INVALID",
        disposition="NO_GO",
        package_digest=package_digest if isinstance(package_digest, str) else None,
        rows={},
        blockers=normalized,
        ledger_status={},
    )


def _validate_acceptance_package_impl(
    payload: Mapping[str, object],
    *,
    now: datetime,
) -> AcceptanceDisposition:
    """Validate a local package; never contacts targets or changes a gate."""
    if not isinstance(payload, Mapping):
        return _invalid_result(
            payload,
            [("PACKAGE_NOT_OBJECT", "Acceptance package must be a JSON object.")],
        )
    try:
        schema = _schema_for_default()
    except (OSError, json.JSONDecodeError) as exc:
        return _invalid_result(
            payload,
            [("SCHEMA_UNAVAILABLE", _safe_message(exc))],
        )
    schema_errors = _schema_errors(payload, schema)
    state = _ValidationState(global_blockers={}, row_states={}, structural_errors={})
    if schema_errors:
        for path, message in schema_errors:
            state.add_global(
                "SCHEMA_VALIDATION_FAILED",
                f"{path}: {message}",
                structural=True,
            )

    run_id = payload.get("run_id")
    manifest = payload.get("manifest")
    rows = payload.get("rows")
    if not isinstance(manifest, Mapping):
        state.add_global("MANIFEST_MISSING", "Package manifest is missing.", structural=True)
        manifest = {}
    if not isinstance(rows, list):
        state.add_global("ROWS_MISSING", "Package row array is missing.", structural=True)
        rows = []
    if not isinstance(now, datetime) or now.tzinfo is None:
        state.add_global(
            "VALIDATION_TIME_INVALID",
            "The injected validation time must be timezone-aware.",
            structural=True,
        )
        now = datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)

    row_by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            state.add_global("ROW_NOT_OBJECT", "Every row record must be an object.", structural=True)
            continue
        row_id = row.get("row_id")
        if not isinstance(row_id, str) or row_id not in ROW_GATES:
            state.add_global(
                "ROW_ID_UNKNOWN",
                f"Unknown acceptance row ID: {row_id!r}.",
                structural=True,
            )
            continue
        if row_id in row_by_id:
            state.add_global(
                "ROW_ID_DUPLICATE",
                f"Acceptance row {row_id} appears more than once.",
                structural=True,
            )
            continue
        row_by_id[row_id] = row
        state.row_states[row_id] = _RowState(
            claimed_status=str(row.get("status", "NOT RUN")),
            freshness=str(row.get("freshness", {}).get("state", "STALE"))
            if isinstance(row.get("freshness"), Mapping)
            else "STALE",
            blockers=set(),
        )
    missing_rows = set(ROW_IDS) - set(row_by_id)
    if missing_rows:
        state.add_global(
            "ROW_SET_INCOMPLETE",
            "The package must contain all 22 required row IDs exactly once.",
            structural=True,
        )

    if set(row_by_id) != set(ROW_IDS):
        state.add_global(
            "ROW_SET_MISMATCH",
            "The package row IDs do not equal the exact RG0/RG1 acceptance set.",
            structural=True,
        )

    _validate_manifest(payload, manifest, state)
    _validate_record_digests(payload, row_by_id, state)
    p0_status, matrix_status, ledger_errors = _read_ledgers()
    dependencies, matrix_rows, dependency_errors = _compute_dependency_map(
        _load_matrix_safely()
    )
    for error in ledger_errors:
        state.add_global("LEDGER_READ_FAILED", error)
    for error in dependency_errors:
        state.add_global("MATRIX_ROW_MISSING", error)
    _validate_ledgers(payload, p0_status, matrix_status, matrix_rows, state)

    for row_id, row in row_by_id.items():
        _validate_row(
            row_id=row_id,
            row=row,
            payload=payload,
            manifest=manifest,
            now=now,
            expected_dependencies=dependencies.get(row_id, ()),
            state=state,
        )

    _validate_invalidations(payload, dependencies, state)
    _validate_evidence_index(payload, state)

    p0_rg0_pass = (
        _is_pass(p0_status.get("RG0"))
        and _is_pass(p0_status.get("release_status"))
        and p0_status.get("production_effects_enabled") is False
        and bool(p0_status.get("external_checks"))
        and all(_is_pass(status) for status in p0_status.get("external_checks", []))
    )
    matrix_rg1_pass = _is_pass(matrix_status.get("RG1"))
    package_is_external = manifest.get("package_scope") == "EXTERNAL_PRODUCTION"
    if manifest.get("package_scope") == "SYNTHETIC":
        state.add_global(
            "SYNTHETIC_PACKAGE_LOCAL_ONLY",
            "Synthetic packages can establish local structure only.",
        )
    if not p0_rg0_pass or not matrix_rg1_pass:
        state.add_global(
            "QUALIFICATION_LEDGER_HELD",
            "The reconciled P0/RG0 or P1/RG1 ledger is held or has a non-pass prerequisite.",
        )
    if not package_is_external:
        state.add_global(
            "EXTERNAL_SCOPE_NOT_ESTABLISHED",
            "This package does not claim an external production acceptance scope.",
        )

    _apply_row_holds(
        state=state,
        row_by_id=row_by_id,
        dependencies=dependencies,
        matrix_rows=matrix_rows,
        p0_rg0_pass=p0_rg0_pass,
        matrix_rg1_pass=matrix_rg1_pass,
        package_is_external=package_is_external,
    )
    all_rows_pass = set(row_by_id) == set(ROW_IDS) and all(
        row_id in state.row_states
        and state.row_states[row_id].claimed_status == "PASS"
        and state.row_states[row_id].freshness == "CURRENT"
        and not state.row_states[row_id].blockers
        for row_id in ROW_IDS
    )
    if not all_rows_pass:
        state.add_global(
            "ROW_STATUS_NOT_PASS",
            "Every required row must have a current, exact-scope PASS disposition.",
        )
    final_audit_status = _validate_final_audit(payload, state, now, row_by_id)
    if not final_audit_status:
        state.add_global(
            "FINAL_AUDIT_NOT_COMPLETE",
            "A separate final audit must recheck all 22 rows before external readiness.",
        )

    rows_out: dict[str, RowDisposition] = {}
    for row_id in ROW_IDS:
        row_state = state.row_states.get(row_id)
        if row_state is None:
            continue
        status = row_state.claimed_status
        blockers = set(row_state.blockers)
        if status not in VALID_STATUSES:
            status = "HELD"
            blockers.add("STATUS_INVALID")
        if "NO_MUTATION_STATE_MISMATCH" in blockers or "NEGATIVE_CONTROL_EFFECT_OBSERVED" in blockers:
            effective = "FAIL"
        elif status in {"FAIL", "NOT RUN", "HELD"}:
            effective = status
        elif blockers:
            effective = "HELD"
        else:
            effective = "PASS"
        local_validation = "VALID" if not blockers.intersection(_LOCAL_BLOCKING_CODES) else "HELD"
        rows_out[row_id] = RowDisposition(
            row_id=row_id,
            claimed_status=row_state.claimed_status,
            local_validation=local_validation,
            effective_status=effective,
            freshness=row_state.freshness,
            blockers=tuple(sorted(blockers)),
        )

    disposition = "NO_GO"
    if (
        not state.global_blockers
        and not state.structural_errors
        and package_is_external
        and p0_rg0_pass
        and matrix_rg1_pass
        and final_audit_status
        and all(row.effective_status == "PASS" for row in rows_out.values())
    ):
        disposition = "READY_FOR_FINAL_AUDIT"

    structural_status = "INVALID" if state.structural_errors else "VALID"
    blockers = tuple(
        {"code": code, "message": message}
        for code, message in state.global_blockers.items()
    )
    return AcceptanceDisposition(
        run_id=run_id if isinstance(run_id, str) else None,
        structural_status=structural_status,
        disposition=disposition,
        package_digest=(
            payload.get("package_digest")
            if isinstance(payload.get("package_digest"), str)
            else None
        ),
        rows=rows_out,
        blockers=blockers,
        ledger_status={
            "p0": p0_status,
            "p1": matrix_status,
        },
    )


def validate_acceptance_package(
    payload: Mapping[str, object],
    *,
    now: datetime,
) -> AcceptanceDisposition:
    """Return a fail-closed result for malformed local package shapes."""
    try:
        return _validate_acceptance_package_impl(payload, now=now)
    except Exception as exc:
        return _invalid_result(
            payload,
            [
                (
                    "PACKAGE_VALIDATION_ERROR",
                    f"Package validation failed ({type(exc).__name__}).",
                )
            ],
        )
