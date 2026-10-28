from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ._canonical import _evidence_ref_error, _is_sha256, _iter_evidence_refs, _parse_datetime
from ._contracts import ROW_GATES, ROW_IDS, _ValidationState
from ._reconciliation import _transitive_dependents
from ._row_checks import _validate_person
from ._scope_binding import _authorization_scope_from_manifest, _route_set_binding


def _validate_invalidations(
    payload: Mapping[str, Any],
    dependencies: Mapping[str, tuple[str, ...]],
    state: _ValidationState,
) -> None:
    records = payload.get("invalidation_records", [])
    if not isinstance(records, list):
        state.add_global("INVALIDATION_LOG_INVALID", "Invalidation history must be an array.", structural=True)
        return
    lineage = payload.get("lineage")
    if not isinstance(lineage, Mapping):
        lineage = {}
        state.add_global(
            "LINEAGE_DECLARATION_INVALID",
            "The package must explicitly declare a root or a predecessor-linked successor.",
            structural=True,
        )
    lineage_kind = lineage.get("kind")
    predecessor_run_id = lineage.get("predecessor_run_id")
    predecessor_digest = lineage.get("predecessor_package_sha256")
    if lineage_kind == "ROOT":
        if records:
            state.add_global(
                "LINEAGE_DECLARATION_INVALID",
                "A lineage root cannot silently carry prior-run invalidation history.",
                structural=True,
            )
    elif lineage_kind == "SUCCESSOR":
        if (
            not isinstance(predecessor_run_id, str)
            or not predecessor_run_id
            or predecessor_run_id == payload.get("run_id")
            or not _is_sha256(predecessor_digest)
            or not records
            or predecessor_digest == payload.get("package_digest")
        ):
            state.add_global(
                "LINEAGE_PREDECESSOR_INVALID",
                "A successor must identify a distinct immutable predecessor and carry append-only invalidations.",
                structural=True,
            )
    else:
        state.add_global(
            "LINEAGE_DECLARATION_INVALID",
            "Lineage kind must be ROOT or SUCCESSOR.",
            structural=True,
        )
    ids: set[str] = set()
    sequences: list[int] = []
    predecessor_link_found = False
    row_states = state.row_states
    for record in records:
        if not isinstance(record, Mapping):
            state.add_global("INVALIDATION_LOG_INVALID", "Invalidation record must be an object.", structural=True)
            continue
        invalidation_id = record.get("invalidation_id")
        sequence = record.get("sequence")
        if not isinstance(invalidation_id, str) or invalidation_id in ids:
            state.add_global(
                "INVALIDATION_APPEND_ONLY_VIOLATION",
                "Invalidation IDs must be unique append-only records.",
                structural=True,
            )
        ids.add(str(invalidation_id))
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            state.add_global(
                "INVALIDATION_SEQUENCE_INVALID",
                "Invalidation sequence must be an integer.",
                structural=True,
            )
        else:
            sequences.append(sequence)
        if (
            record.get("source_run_id") == payload.get("run_id")
            or record.get("new_run_id") != payload.get("run_id")
            or not _is_sha256(record.get("source_package_sha256"))
            or not record.get("reason")
            or _parse_datetime(record.get("invalidated_at")) is None
        ):
            state.add_global(
                "INVALIDATION_LINEAGE_INVALID",
                "Invalidation must preserve a prior immutable run digest, name the new run, and use UTC time.",
                structural=True,
            )
        if (
            lineage_kind == "SUCCESSOR"
            and record.get("source_run_id") == predecessor_run_id
            and record.get("source_package_sha256") == predecessor_digest
        ):
            predecessor_link_found = True
        scope = record.get("scope")
        source_row = record.get("source_row_id")
        if scope == "COMMON":
            expected_affected = set(ROW_IDS)
        elif scope == "ROW" and source_row in ROW_GATES:
            expected_affected = _transitive_dependents(source_row, dependencies)
        else:
            expected_affected = set()
        actual_affected = set(record.get("affected_row_ids", []))
        if not expected_affected or actual_affected != expected_affected:
            state.add_global(
                "INVALIDATION_CLOSURE_MISMATCH",
                "Invalidation affected rows must equal the complete transitive dependency closure.",
                structural=True,
            )
        for row_id in actual_affected & set(row_states):
            row_states[row_id].blockers.add("EVIDENCE_INVALIDATED")
    if sequences and sequences != list(range(1, len(sequences) + 1)):
        state.add_global(
            "INVALIDATION_APPEND_ONLY_VIOLATION",
            "Invalidation sequence numbers must be contiguous and strictly append-only.",
            structural=True,
        )
    if len(sequences) != len(set(sequences)):
        state.add_global(
            "INVALIDATION_APPEND_ONLY_VIOLATION",
            "Invalidation sequence numbers must be unique.",
            structural=True,
        )
    if lineage_kind == "SUCCESSOR" and not predecessor_link_found:
        state.add_global(
            "LINEAGE_PREDECESSOR_INVALID",
            "Successor invalidation history must bind the declared predecessor package digest.",
            structural=True,
        )


def _validate_final_audit(
    payload: Mapping[str, Any],
    state: _ValidationState,
    now: datetime,
    row_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    audit = payload.get("final_audit")
    if not isinstance(audit, Mapping):
        return False
    rows = audit.get("rechecked_row_ids", [])
    rechecks = audit.get("row_rechecks", [])
    rechecked_ids = [item.get("row_id") for item in rechecks if isinstance(item, Mapping)]
    audit_time = _parse_datetime(audit.get("audited_at"))
    auditor = audit.get("auditor")
    manifest = payload.get("manifest", {})
    expected_scope = (
        _authorization_scope_from_manifest(manifest, _route_set_binding(manifest))
        if isinstance(manifest, Mapping)
        else None
    )
    if (
        audit_time is None
        or audit_time > now
        or _validate_person(
            auditor,
            now,
            expected_role="independent-final-auditor",
            expected_scope=expected_scope,
        )
    ):
        state.add_global(
            "FINAL_AUDITOR_AUTHORITY_INVALID",
            "The separate final audit requires a named, current independent auditor.",
        )
    row_authorities = {
        person.get("person_id")
        for row in row_by_id.values()
        for role in ("owner", "operator", "independent_reviewer")
        for person in (row.get(role),)
        if isinstance(person, Mapping)
    }
    if isinstance(auditor, Mapping) and auditor.get("person_id") in row_authorities:
        state.add_global(
            "FINAL_AUDITOR_NOT_INDEPENDENT",
            "The final auditor must differ from every row owner, operator, and reviewer.",
        )
    if (
        audit.get("record_type") != "SEPARATE_FINAL_AUDIT"
        or audit.get("package_digest") != payload.get("package_digest")
        or len(rows) != len(ROW_IDS)
        or set(rows) != set(ROW_IDS)
        or len(rechecked_ids) != len(ROW_IDS)
        or set(rechecked_ids) != set(ROW_IDS)
    ):
        state.add_global(
            "FINAL_AUDIT_ROW_SET_MISMATCH",
            "Separate final audit must bind this package digest and recheck all 22 rows once.",
            structural=True,
        )
        return False
    rechecks_complete = all(
        item.get("status") == "PASS"
        and item.get("row_id") in row_by_id
        and row_by_id[item["row_id"]].get("status") == "PASS"
        and item.get("row_id") in state.row_states
        and not state.row_states[item["row_id"]].blockers
        and _evidence_ref_error(item.get("readback")) is None
        for item in rechecks
        if isinstance(item, Mapping)
    )
    if not rechecks_complete:
        state.add_global(
            "FINAL_AUDIT_ROW_RECHECK_INCOMPLETE",
            "The final audit must independently read back every current PASS row.",
        )
    return (
        rechecks_complete
        and audit_time is not None
        and audit_time <= now
        and not _validate_person(
            auditor,
            now,
            expected_role="independent-final-auditor",
            expected_scope=expected_scope,
        )
        and isinstance(auditor, Mapping)
        and auditor.get("person_id") not in row_authorities
        and audit.get("status") == "PASS"
        and audit.get("disposition") == "READY_FOR_FINAL_AUDIT"
    )


def _validate_evidence_index(
    payload: Mapping[str, Any],
    state: _ValidationState,
) -> None:
    source = {
        "manifest": payload.get("manifest"),
        "manifest_signature": payload.get("manifest_signature"),
        "rows": payload.get("rows"),
        "invalidation_records": payload.get("invalidation_records"),
        "final_audit": payload.get("final_audit"),
    }
    expected = [
        {
            "uri": reference["uri"],
            "sha256": reference["sha256"],
            "retention_mode": reference["retention_mode"],
        }
        for reference in _iter_evidence_refs(source)
    ]
    actual = payload.get("artifact_digest_index", [])
    if not isinstance(actual, list):
        state.add_global(
            "EVIDENCE_INDEX_INVALID",
            "Artifact digest index must be an array.",
            structural=True,
        )
        return
    normalized = []
    for reference in actual:
        if not isinstance(reference, Mapping):
            state.add_global(
                "EVIDENCE_INDEX_INVALID",
                "Artifact digest index entries must be objects.",
                structural=True,
            )
            return
        normalized.append(
            {
                "uri": reference.get("uri"),
                "sha256": reference.get("sha256"),
                "retention_mode": reference.get("retention_mode"),
            }
        )
    if normalized != expected:
        state.add_global(
            "EVIDENCE_INDEX_MISMATCH",
            "Artifact digest index must enumerate every immutable evidence reference exactly once.",
            structural=True,
        )
    for reference in _iter_evidence_refs(source):
        code = _evidence_ref_error(reference)
        if code:
            state.add_global(
                "EVIDENCE_REFERENCE_INVALID",
                f"An immutable evidence reference is invalid: {code}.",
                structural=True,
            )
            break
