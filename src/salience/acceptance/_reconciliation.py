from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ._canonical import _is_pass
from ._contracts import (
    FALLBACK_DEPENDENCIES,
    P0_RELEASE_PATH,
    P1_QUALIFICATION_MATRIX_PATH,
    RG1_ROW_IDS,
    ROW_GATES,
    ROW_IDS,
    _ValidationState,
)


P0_EXTERNAL_CHECKS = frozenset(
    {
        "independent safety review",
        "production identity and ingress",
        "production storage and restore",
        "production collector and alert delivery",
        "remote repository gate enforcement",
    }
)
LEDGER_STATUSES = frozenset({"PASS", "FAIL", "NOT RUN", "HELD"})


def _compute_dependency_map(matrix: Any) -> tuple[dict[str, tuple[str, ...]], dict[str, Mapping[str, Any]], list[str]]:
    if not isinstance(matrix, Mapping):
        return dict(FALLBACK_DEPENDENCIES), {}, ["qualification matrix is not an object"]
    requirements = matrix.get("requirements")
    if not isinstance(requirements, list):
        return dict(FALLBACK_DEPENDENCIES), {}, ["qualification matrix requirements are missing"]
    indexed: dict[str, Mapping[str, Any]] = {}
    duplicates: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        row_id = requirement.get("id")
        if isinstance(row_id, str):
            if row_id in indexed:
                duplicates.add(row_id)
            indexed[row_id] = requirement
    missing = [row_id for row_id in RG1_ROW_IDS if row_id not in indexed]
    unexpected = sorted(set(indexed) - set(RG1_ROW_IDS))
    dependencies: dict[str, tuple[str, ...]] = {}
    for row_id in RG1_ROW_IDS:
        requirement = indexed.get(row_id)
        raw = requirement.get("dependencies") if requirement is not None else None
        if not isinstance(raw, str):
            dependencies[row_id] = FALLBACK_DEPENDENCIES[row_id]
            continue
        values = tuple(part.strip() for part in raw.split(",") if part.strip())
        dependencies[row_id] = values
    errors = [f"missing matrix row {row_id}" for row_id in missing]
    errors.extend(f"duplicate matrix row ID {row_id}" for row_id in sorted(duplicates))
    errors.extend(f"unexpected matrix row ID {row_id}" for row_id in unexpected)
    return dependencies, indexed, errors


def _read_ledgers(
    p0_path: Path = P0_RELEASE_PATH,
    matrix_path: Path = P1_QUALIFICATION_MATRIX_PATH,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        p0 = json.loads(p0_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        p0 = {}
        errors.append(f"p0 release ledger unavailable: {exc}")
    try:
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        matrix = {}
        errors.append(f"P1 qualification matrix unavailable: {exc}")

    dependencies, matrix_rows, dependency_errors = _compute_dependency_map(matrix)
    errors.extend(dependency_errors)
    p0_checks = p0.get("external_checks", [])
    check_statuses: list[str] = []
    check_names: list[str] = []
    if isinstance(p0_checks, list):
        for check in p0_checks:
            if isinstance(check, Mapping):
                name = check.get("check")
                if isinstance(name, str) and name:
                    check_names.append(name)
                status = check.get("status")
                check_statuses.append(status if isinstance(status, str) else "HELD")
        if (
            len(p0_checks) != len(P0_EXTERNAL_CHECKS)
            or len(check_statuses) != len(P0_EXTERNAL_CHECKS)
            or len(check_names) != len(P0_EXTERNAL_CHECKS)
            or len(set(check_names)) != len(P0_EXTERNAL_CHECKS)
            or set(check_names) != P0_EXTERNAL_CHECKS
        ):
            errors.append("p0 release ledger must contain the exact five external check identities once")
        if any(status not in LEDGER_STATUSES for status in check_statuses):
            errors.append("p0 external check status is invalid")
    else:
        errors.append("p0 external checks are missing or malformed")

    p0_status = {
        "release_gate": p0.get("release_gate", "HELD"),
        "release_status": p0.get("release_status", "HELD"),
        "production_effects_enabled": p0.get("production_effects_enabled"),
        "RG0": p0.get("RG0", "HELD"),
        "RG1": p0.get("RG1", "HELD"),
        "external_checks": check_statuses,
    }
    matrix_status = {
        "RG0": matrix.get("RG0", "HELD") if isinstance(matrix, Mapping) else "HELD",
        "RG1": matrix.get("RG1", "HELD") if isinstance(matrix, Mapping) else "HELD",
        "requirements": {
            row_id: {
                "gate": matrix_rows[row_id].get("gate"),
                "production_release_status": matrix_rows[row_id].get(
                    "production_release_status", "HELD"
                ),
                "dependencies": list(dependencies.get(row_id, ())),
            }
            for row_id in RG1_ROW_IDS
            if row_id in matrix_rows
        },
    }
    if p0_status["release_gate"] != "RG0":
        errors.append("p0 release gate must be RG0")
    if any(
        p0_status.get(key) not in LEDGER_STATUSES
        for key in ("release_status", "RG0", "RG1")
    ):
        errors.append("p0 RG0/RG1 release summary status is invalid")
    if any(matrix_status.get(key) not in LEDGER_STATUSES for key in ("RG0", "RG1")):
        errors.append("P1 RG0/RG1 qualification summary status is invalid")
    return p0_status, matrix_status, errors


def _dependencies_for_rows(
    matrix_dependencies: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    return {
        row_id: matrix_dependencies.get(row_id, ())
        for row_id in ROW_IDS
    }


def _transitive_dependents(source_row_id: str, dependencies: Mapping[str, Sequence[str]]) -> set[str]:
    affected = {source_row_id}
    changed = True
    while changed:
        changed = False
        for candidate, required in dependencies.items():
            if candidate not in affected and any(dependency in affected for dependency in required):
                affected.add(candidate)
                changed = True
    return affected


def _load_matrix_safely() -> Any:
    try:
        return json.loads(P1_QUALIFICATION_MATRIX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _validate_ledgers(
    payload: Mapping[str, Any],
    p0_status: Mapping[str, Any],
    matrix_status: Mapping[str, Any],
    matrix_rows: Mapping[str, Mapping[str, Any]],
    state: _ValidationState,
) -> None:
    rows = {
        row.get("row_id"): row
        for row in payload.get("rows", [])
        if isinstance(row, Mapping)
    }
    for row_id in RG1_ROW_IDS:
        ledger_row = matrix_rows.get(row_id)
        if ledger_row is None:
            state.add_global(
                "MATRIX_ROW_MISSING",
                f"Qualification matrix has no current entry for {row_id}.",
                structural=True,
            )
            continue
        dependencies = tuple(
            part.strip()
            for part in str(ledger_row.get("dependencies", "")).split(",")
            if part.strip()
        )
        expected = FALLBACK_DEPENDENCIES[row_id]
        if dependencies != expected:
            state.add_global(
                "MATRIX_DEPENDENCY_MISMATCH",
                f"Qualification matrix dependency contract changed for {row_id}.",
                structural=True,
            )
        row = rows.get(row_id)
        if isinstance(row, Mapping) and row.get("gate") != ledger_row.get("gate"):
            state.add_row(
                row_id,
                "GATE_STATUS_MISMATCH",
                f"Package gate for {row_id} differs from the current matrix.",
                structural=True,
            )
    if p0_status.get("production_effects_enabled") is not False:
        state.add_global(
            "P0_LEDGER_EFFECTS_ENABLED",
            "The P0 release ledger does not record effects disabled.",
        )


def _apply_row_holds(
    *,
    state: _ValidationState,
    row_by_id: Mapping[str, Mapping[str, Any]],
    dependencies: Mapping[str, Sequence[str]],
    matrix_rows: Mapping[str, Mapping[str, Any]],
    p0_rg0_pass: bool,
    matrix_rg1_pass: bool,
    package_is_external: bool,
) -> None:
    """Apply reconciled ledger and transitive prerequisite blockers to claimed PASS rows."""
    for row_id, row_state in state.row_states.items():
        if row_state.claimed_status != "PASS":
            continue
        if not package_is_external:
            row_state.blockers.add("SYNTHETIC_PACKAGE_LOCAL_ONLY")
        if ROW_GATES[row_id] == "RG0" and not p0_rg0_pass:
            row_state.blockers.add("QUALIFICATION_LEDGER_HELD")
        if ROW_GATES[row_id] == "RG1":
            ledger_row = matrix_rows.get(row_id, {})
            if (
                ledger_row.get("gate") != "RG1"
                or not _is_pass(ledger_row.get("production_release_status"))
                or not matrix_rg1_pass
            ):
                row_state.blockers.add("QUALIFICATION_LEDGER_HELD")

    changed = True
    while changed:
        changed = False
        for row_id, row_state in state.row_states.items():
            if row_state.claimed_status != "PASS":
                continue
            for dependency in dependencies.get(row_id, ()):
                dependency_passes = True
                if dependency == "P0":
                    dependency_passes = p0_rg0_pass
                else:
                    dependency_state = state.row_states.get(dependency)
                    dependency_passes = (
                        dependency_state is not None
                        and dependency_state.claimed_status == "PASS"
                        and dependency_state.freshness == "CURRENT"
                        and not dependency_state.blockers
                    )
                if not dependency_passes and "DEPENDENCY_NOT_PASS" not in row_state.blockers:
                    row_state.blockers.add("DEPENDENCY_NOT_PASS")
                    changed = True
