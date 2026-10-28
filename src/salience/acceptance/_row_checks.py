from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ._canonical import _evidence_ref_error, _is_sha256, _iter_evidence_refs, _parse_datetime
from ._contracts import REQUIRED_NEGATIVE_CONTROL_ROW_IDS, ROW_GATES, SECRET_KEY_RE, SECRET_VALUE_RE
from ._scope_binding import _authorization_scope_from_manifest, _route_binding_from_row


def _valid_name(person: Any) -> bool:
    if not isinstance(person, Mapping):
        return False
    name = person.get("name")
    if not isinstance(name, str) or len(name.split()) < 2:
        return False
    normalized = name.strip().casefold()
    return normalized not in {"unknown", "tbd", "owner", "operator", "reviewer", "n/a"}


def _validate_person(
    person: Any,
    now: datetime,
    *,
    expected_role: str,
    expected_scope: Mapping[str, Any] | None = None,
) -> list[str]:
    if not isinstance(person, Mapping):
        return ["PERSON_AUTHORIZATION_MISSING"]
    blockers: list[str] = []
    if not _valid_name(person):
        blockers.append("NAMED_HUMAN_REQUIRED")
    if person.get("role") != expected_role:
        blockers.append("PERSON_ROLE_MISMATCH")
    if not person.get("verified_identity") or not person.get("identity_source"):
        blockers.append("IDENTITY_NOT_VERIFIED")
    if not person.get("scope"):
        blockers.append("AUTHORIZATION_SCOPE_MISSING")
    elif expected_scope is not None and _normalized_authorization_scope(person.get("scope")) != _normalized_authorization_scope(expected_scope):
        blockers.append("AUTHORIZATION_SCOPE_MISMATCH")
    if _evidence_ref_error(person.get("authorization_ref")):
        blockers.append("AUTHORIZATION_REFERENCE_INVALID")
    valid_from = _parse_datetime(person.get("valid_from"))
    valid_until = _parse_datetime(person.get("valid_until"))
    if valid_from is None or valid_until is None or valid_from > now or valid_until <= now:
        blockers.append("AUTHORIZATION_EXPIRED_OR_INVALID")
    if not isinstance(person.get("person_id"), str) or not person["person_id"]:
        blockers.append("PERSON_ID_MISSING")
    return blockers


def _normalized_authorization_scope(scope: Any) -> Any:
    if not isinstance(scope, Mapping):
        return scope
    normalized = dict(scope)
    for key in ("artifact_digests", "resource_ids", "data_jurisdiction"):
        values = normalized.get(key)
        if isinstance(values, list):
            normalized[key] = sorted(values)
    route_binding = normalized.get("route_binding")
    if isinstance(route_binding, Mapping) and isinstance(route_binding.get("routes"), list):
        normalized["route_binding"] = {
            **route_binding,
            "routes": sorted(
                route_binding["routes"],
                key=lambda item: str(item.get("route_id")) if isinstance(item, Mapping) else "",
            ),
        }
    return normalized


def _delegation_authorizes(
    principal: Any,
    operator: Any,
    delegation: Any,
    expected_scope: Mapping[str, Any],
) -> bool:
    operator_id = operator.get("person_id") if isinstance(operator, Mapping) else None
    if principal == operator_id and isinstance(operator_id, str):
        return True
    return (
        isinstance(principal, str)
        and isinstance(operator_id, str)
        and isinstance(delegation, Mapping)
        and delegation.get("principal_id") == principal
        and delegation.get("authorized_by") == operator_id
        and _normalized_authorization_scope(delegation.get("scope"))
        == _normalized_authorization_scope(expected_scope)
        and _evidence_ref_error(delegation.get("evidence_ref")) is None
    )


def _validate_people(row: Mapping[str, Any], now: datetime, manifest: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    owner = row.get("owner")
    operator = row.get("operator")
    reviewer = row.get("independent_reviewer")
    expected_scope = _authorization_scope_from_manifest(
        manifest,
        _route_binding_from_row(row),
    )
    blockers.extend(
        _validate_person(owner, now, expected_role="accountable-row-owner", expected_scope=expected_scope)
    )
    blockers.extend(
        _validate_person(operator, now, expected_role="authorized-operator", expected_scope=expected_scope)
    )
    blockers.extend(
        _validate_person(reviewer, now, expected_role="independent-reviewer", expected_scope=expected_scope)
    )
    if isinstance(operator, Mapping) and isinstance(reviewer, Mapping):
        if operator.get("person_id") == reviewer.get("person_id"):
            blockers.append("REVIEWER_OPERATOR_COLLISION")
    if isinstance(reviewer, Mapping) and row.get("reviewer_decision", {}).get("name") != reviewer.get("name"):
        blockers.append("REVIEWER_IDENTITY_MISMATCH")
    if isinstance(operator, Mapping) and row.get("operator_declaration", {}).get("name") != operator.get("name"):
        blockers.append("OPERATOR_IDENTITY_MISMATCH")
    return blockers


def _manifest_row_scope_errors(row: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    candidate = manifest.get("candidate", {})
    deployment = manifest.get("deployment", {})
    environment = manifest.get("environment", {})
    account = environment.get("account_identity", {})
    row_scope = row.get("scope", {})
    row_candidate = row_scope.get("candidate", {})
    row_deployment = row_scope.get("deployment", {})
    row_environment = row_scope.get("environment", {})
    row_account = row_scope.get("account", {})
    errors: list[str] = []
    expected_artifacts = sorted(
        item.get("sha256")
        for item in candidate.get("artifacts", [])
        if isinstance(item, Mapping)
    )
    checks = [
        (row_candidate.get("source_commit"), candidate.get("source_commit")),
        (row_candidate.get("source_tree"), candidate.get("source_tree")),
        (sorted(row_candidate.get("artifact_digests", [])), expected_artifacts),
        (row_candidate.get("migration_head"), candidate.get("migration_head")),
        (row_deployment.get("deployment_id"), deployment.get("deployment_id")),
        (row_deployment.get("database_cluster_alias"), deployment.get("database", {}).get("cluster_alias")),
        (row_deployment.get("database_schema_head"), deployment.get("database", {}).get("schema_head")),
        (row_deployment.get("temporal_target_alias"), deployment.get("temporal", {}).get("target_alias")),
        (row_deployment.get("temporal_namespace"), deployment.get("temporal", {}).get("namespace")),
        (row_deployment.get("storage_config_revision"), deployment.get("storage_config_revision")),
        (row_deployment.get("telemetry_config_revision"), deployment.get("telemetry_config_revision")),
        (row_environment.get("environment_id"), environment.get("environment_id")),
        (row_environment.get("stage"), environment.get("stage")),
        (row_environment.get("region"), environment.get("region")),
        (row_environment.get("tenant_id"), environment.get("tenant_id")),
        (row_environment.get("workspace_id"), environment.get("workspace_id")),
        (row_environment.get("data_jurisdiction"), environment.get("data_jurisdiction")),
        (row_account.get("provider"), account.get("provider")),
        (row_account.get("account_id"), account.get("account_id")),
        (row_account.get("repository_id"), account.get("repository_id")),
        (sorted(row_scope.get("resource_ids", [])), sorted(environment.get("resource_ids", []))),
        (
            row_scope.get("route", {}).get("authenticated_route_manifest_sha256"),
            manifest.get("route_manifest", {}).get("authenticated_manifest_sha256"),
        ),
    ]
    if any(actual != expected for actual, expected in checks):
        errors.append("SCOPE_IDENTITY_MISMATCH")
    try:
        uuid.UUID(str(row_scope.get("route", {}).get("route_id")))
    except (ValueError, AttributeError, TypeError):
        errors.append("ROUTE_ID_NOT_UUID")
    route = row_scope.get("route", {})
    if row_scope.get("route_evidence_required"):
        if not _is_sha256(route.get("route_tuple_sha256")):
            errors.append("ROUTE_TUPLE_DIGEST_MISSING")
        if not _is_sha256(route.get("authenticated_route_manifest_sha256")):
            errors.append("ROUTE_MANIFEST_DIGEST_MISSING")
    return errors


def _validate_procedure(row: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    procedure = row.get("procedure", {})
    blockers: list[str] = []
    if _evidence_ref_error(procedure.get("runbook")):
        blockers.append("RUNBOOK_NOT_PINNED")
    if not procedure.get("runbook_version") or not procedure.get("expected_predicate"):
        blockers.append("PROCEDURE_NOT_EXECUTABLE")
    executable = procedure.get("executable", {})
    kind = executable.get("kind")
    if kind == "CLI":
        if not executable.get("executable") or not executable.get("version") or not executable.get("argv"):
            blockers.append("PROCEDURE_NOT_EXECUTABLE")
        if not procedure.get("command_ids"):
            blockers.append("COMMAND_ID_MISSING")
    elif kind == "API":
        if (
            not executable.get("executable")
            or not executable.get("version")
            or not executable.get("method")
            or not executable.get("endpoint")
            or not _is_sha256(executable.get("request_digest"))
        ):
            blockers.append("PROCEDURE_NOT_EXECUTABLE")
        if not procedure.get("request_ids"):
            blockers.append("REQUEST_ID_MISSING")
    else:
        blockers.append("PROCEDURE_KIND_INVALID")
    if not procedure.get("principal") or not procedure.get("trace_ids"):
        blockers.append("PROCEDURE_CORRELATION_MISSING")
    if not _delegation_authorizes(
        procedure.get("principal"),
        row.get("operator"),
        procedure.get("delegation"),
        _authorization_scope_from_manifest(manifest, _route_binding_from_row(row)),
    ):
        blockers.append("PROCEDURE_PRINCIPAL_UNAUTHORIZED")
    started_at = _parse_datetime(procedure.get("started_at"))
    completed_at = _parse_datetime(procedure.get("completed_at"))
    if started_at is None or completed_at is None or completed_at < started_at:
        blockers.append("PROCEDURE_TIME_INVALID")
    if _evidence_ref_error(row.get("raw_result")) or _evidence_ref_error(row.get("readback")):
        blockers.append("RAW_RESULT_OR_READBACK_MISSING")
    return blockers


def _validate_freshness(
    row: Mapping[str, Any],
    now: datetime,
) -> tuple[str, list[str]]:
    freshness = row.get("freshness", {})
    blockers: list[str] = []
    state = freshness.get("state")
    if state == "STALE":
        blockers.append("STALE")
    observed_at = _parse_datetime(freshness.get("observed_at"))
    max_age = freshness.get("max_age_seconds")
    policy = freshness.get("policy", {})
    approved_at = _parse_datetime(policy.get("approved_at"))
    expires_at = _parse_datetime(policy.get("expires_at"))
    if (
        isinstance(max_age, bool)
        or not isinstance(max_age, int)
        or max_age <= 0
        or not policy.get("approved")
        or not policy.get("version")
        or approved_at is None
        or approved_at > now
        or expires_at is None
        or expires_at <= now
        or _evidence_ref_error(policy.get("evidence_ref"))
    ):
        blockers.append("FRESHNESS_POLICY_INVALID")
    if observed_at is None or observed_at > now:
        blockers.append("FRESHNESS_OBSERVATION_INVALID")
    elif isinstance(max_age, int) and not isinstance(max_age, bool) and max_age > 0:
        age_seconds = (now - observed_at).total_seconds()
        if age_seconds > max_age:
            blockers.append("STALE")
    if (
        freshness.get("source_state_sha256") != freshness.get("final_reviewed_state_sha256")
        and row.get("transition", {}).get("mode") == "NO_MUTATION"
    ):
        blockers.append("STALE")
    if "STALE" in blockers:
        return "STALE", sorted(set(blockers))
    return "CURRENT", sorted(set(blockers))


def _validate_transition(
    row: Mapping[str, Any], manifest: Mapping[str, Any]
) -> tuple[list[str], str | None]:
    transition = row.get("transition", {})
    mode = transition.get("mode")
    rollback = transition.get("rollback", {})
    blockers: list[str] = []
    failure_status: str | None = None
    if not transition.get("owner_acknowledged") or not transition.get("reviewer_acknowledged"):
        blockers.append("TRANSITION_ACKNOWLEDGEMENT_MISSING")
    if mode == "NO_MUTATION":
        if transition.get("mutation_ids"):
            blockers.append("NO_MUTATION_HAS_MUTATION_IDS")
        if transition.get("pre_state_sha256") != transition.get("post_state_sha256"):
            blockers.append("NO_MUTATION_STATE_MISMATCH")
            failure_status = "FAIL"
        if _evidence_ref_error(transition.get("pre_state_readback")) or _evidence_ref_error(
            transition.get("post_state_readback")
        ):
            blockers.append("NO_MUTATION_READBACK_MISSING")
        if rollback.get("status") != "VERIFIED_NO_MUTATION":
            blockers.append("ROLLBACK_UNVERIFIED")
        if rollback.get("unresolved_liabilities"):
            blockers.append("ROLLBACK_LIABILITY_UNRESOLVED")
        if rollback.get("post_state_sha256") != transition.get("post_state_sha256"):
            blockers.append("ROLLBACK_POST_STATE_MISMATCH")
        if _evidence_ref_error(rollback.get("procedure")) or _evidence_ref_error(
            rollback.get("pre_state")
        ) or _evidence_ref_error(rollback.get("post_state")):
            blockers.append("ROLLBACK_EVIDENCE_MISSING")
    elif mode == "PLANNED_TRANSITION":
        required = (
            "change_ticket",
            "target_scope",
            "expected_pre_version",
            "expected_post_version",
            "observed_pre_version",
            "observed_post_version",
            "actor",
            "command_receipt",
        )
        if any(not transition.get(field) for field in required):
            blockers.append("PLANNED_TRANSITION_UNPINNED")
        if transition.get("target_scope") != row.get("scope"):
            blockers.append("PLANNED_TRANSITION_SCOPE_MISMATCH")
        if not _delegation_authorizes(
            transition.get("actor"),
            row.get("operator"),
            transition.get("delegation"),
            _authorization_scope_from_manifest(manifest, _route_binding_from_row(row)),
        ):
            blockers.append("TRANSITION_ACTOR_UNAUTHORIZED")
        if (
            transition.get("expected_pre_version") != transition.get("observed_pre_version")
            or transition.get("expected_post_version") != transition.get("observed_post_version")
        ):
            blockers.append("PLANNED_TRANSITION_MISMATCH")
        freshness = row.get("freshness", {})
        if (
            transition.get("post_state_sha256") != freshness.get("final_reviewed_state_sha256")
            or transition.get("expected_post_version") != freshness.get("final_reviewed_version")
            or transition.get("final_reviewed_version") != freshness.get("final_reviewed_version")
        ):
            blockers.append("PLANNED_TRANSITION_FRESHNESS_MISMATCH")
        if _evidence_ref_error(transition.get("command_receipt")):
            blockers.append("PLANNED_TRANSITION_RECEIPT_MISSING")
        if not transition.get("mutation_ids"):
            blockers.append("PLANNED_TRANSITION_MUTATION_ID_MISSING")
        if rollback.get("status") not in {
            "VERIFIED",
            "VERIFIED_FINAL_REVOKED_STATE",
        }:
            blockers.append("ROLLBACK_UNVERIFIED")
        if _evidence_ref_error(rollback.get("procedure")) or _evidence_ref_error(
            rollback.get("pre_state")
        ) or _evidence_ref_error(rollback.get("post_state")):
            blockers.append("ROLLBACK_EVIDENCE_MISSING")
        if _evidence_ref_error(rollback.get("action_receipt")):
            blockers.append("ROLLBACK_ACTION_READBACK_MISSING")
        if rollback.get("post_state_sha256") != transition.get("post_state_sha256"):
            blockers.append("ROLLBACK_POST_STATE_MISMATCH")
        if rollback.get("unresolved_liabilities"):
            blockers.append("ROLLBACK_LIABILITY_UNRESOLVED")
        if not rollback.get("owner_acknowledged") or not rollback.get("reviewer_acknowledged"):
            blockers.append("ROLLBACK_ACKNOWLEDGEMENT_MISSING")
        if transition.get("subsequent_revision") and (
            transition.get("subsequent_revision") != transition.get("final_reviewed_version")
            or transition.get("final_reviewed_version")
            != transition.get("expected_post_version")
        ):
            blockers.append("UNPLANNED_REVISION_DRIFT")
        if transition.get("subsequent_revision"):
            blockers.append("UNPLANNED_REVISION_DRIFT")
    else:
        blockers.append("TRANSITION_MODE_INVALID")
    scope = row.get("scope", {})
    if rollback.get("scope") != scope:
        blockers.append("ROLLBACK_SCOPE_MISMATCH")
    return sorted(set(blockers)), failure_status


def _validate_negative_control(row: Mapping[str, Any], row_id: str) -> list[str]:
    control = row.get("negative_control", {})
    if not control.get("required"):
        return (
            ["NEGATIVE_CONTROL_REQUIRED"]
            if row_id in REQUIRED_NEGATIVE_CONTROL_ROW_IDS
            else []
        )
    blockers: list[str] = []
    if control.get("expected_outcome") != "DENIED" or control.get("observed_outcome") != "DENIED":
        blockers.append("NEGATIVE_CONTROL_DENIAL_UNPROVEN")
    if _evidence_ref_error(control.get("raw_denial")):
        blockers.append("NEGATIVE_CONTROL_RAW_DENIAL_MISSING")
    if _evidence_ref_error(control.get("independent_effect_readback")):
        blockers.append("NEGATIVE_CONTROL_EFFECT_READBACK_MISSING")
    if control.get("observed_call_count") != 0 or control.get("observed_effect_count") != 0:
        blockers.append("NEGATIVE_CONTROL_EFFECT_OBSERVED")
    return blockers


def _validate_credentials(row: Mapping[str, Any]) -> list[str]:
    credential = row.get("credential_state", {})
    revoked = set(credential.get("revoked_version_ids", []))
    restored = set(credential.get("restored_version_ids", []))
    blockers: list[str] = []
    if revoked.intersection(restored):
        blockers.append("REVOKED_CREDENTIAL_RESTORED")
    if revoked and (
        not credential.get("revocation_still_effective")
        or credential.get("current_version_id") in revoked
    ):
        blockers.append("REVOKED_CREDENTIAL_RESTORED")
    return blockers


def _validate_observability(row: Mapping[str, Any], run_id: str, manifest: Mapping[str, Any]) -> list[str]:
    observability = row.get("observability", {})
    correlation = observability.get("correlation", {})
    procedure = row.get("procedure", {})
    scope = row.get("scope", {})
    environment = manifest.get("environment", {})
    candidate = manifest.get("candidate", {})
    account = environment.get("account_identity", {})
    expected = {
        "run_id": run_id,
        "candidate_commit": candidate.get("source_commit"),
        "candidate_tree": candidate.get("source_tree"),
        "artifact_digests": sorted(manifest.get("candidate_artifact_digests", [])),
        "deployment_id": manifest.get("deployment", {}).get("deployment_id"),
        "environment_id": environment.get("environment_id"),
        "account_alias": account.get("account_id"),
        "resource_ids": sorted(environment.get("resource_ids", [])),
        "command_ids": procedure.get("command_ids", []),
        "request_ids": procedure.get("request_ids", []),
        "trace_ids": procedure.get("trace_ids", []),
        "route_id": scope.get("route", {}).get("route_id"),
    }
    blockers: list[str] = []
    if any(correlation.get(key) != value for key, value in expected.items()):
        blockers.append("OBSERVABILITY_CORRELATION_MISMATCH")
    timestamps = correlation.get("timestamps_utc", [])
    if (
        not timestamps
        or any(_parse_datetime(timestamp) is None for timestamp in timestamps)
        or not correlation.get("operation_ids")
    ):
        blockers.append("OBSERVABILITY_CORRELATION_INCOMPLETE")
    for collection in ("events", "metrics"):
        refs = observability.get(collection, [])
        if not refs or any(_evidence_ref_error(ref) for ref in refs):
            blockers.append("OBSERVABILITY_ARTIFACT_MISSING")
    if _evidence_ref_error(observability.get("alert_delivery")) or _evidence_ref_error(
        observability.get("human_acknowledgement")
    ):
        blockers.append("OBSERVABILITY_ACKNOWLEDGEMENT_MISSING")
    scan = observability.get("secret_scan", {})
    if not observability.get("secret_free") or scan.get("status") != "PASS" or scan.get("findings"):
        blockers.append("SECRET_FREE_EVIDENCE_UNPROVEN")
    if _evidence_ref_error(scan.get("evidence_ref")):
        blockers.append("SECRET_SCAN_EVIDENCE_MISSING")
    if _contains_secret(row):
        blockers.append("SECRET_FOUND_IN_EVIDENCE")
    return sorted(set(blockers))


def _contains_secret(value: Any, key: str = "") -> bool:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            normalized = str(child_key).replace("-", "_")
            if SECRET_KEY_RE.search(normalized):
                return True
            if _contains_secret(child, normalized):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_secret(child, key) for child in value)
    elif isinstance(value, str):
        if key.casefold() in {"secret_free", "secret_scan", "secret_scan_status"}:
            return False
        return SECRET_VALUE_RE.search(value) is not None
    return False


def _validate_dependency_records(
    row: Mapping[str, Any],
    expected: Sequence[str],
    run_id: str,
    manifest: Mapping[str, Any],
) -> list[str]:
    declared = row.get("dependencies", [])
    evidence = row.get("dependency_evidence", [])
    blockers: list[str] = []
    if list(declared) != list(expected):
        blockers.append("DEPENDENCY_SET_MISMATCH")
    evidence_by_id = {
        item.get("dependency_id"): item
        for item in evidence
        if isinstance(item, Mapping)
    }
    if set(evidence_by_id) != set(expected):
        blockers.append("DEPENDENCY_EVIDENCE_SET_MISMATCH")
        return blockers
    candidate = manifest.get("candidate", {})
    deployment = manifest.get("deployment", {})
    environment = manifest.get("environment", {})
    account = environment.get("account_identity", {})
    for dependency_id in expected:
        item = evidence_by_id[dependency_id]
        if (
            item.get("status") != "PASS"
            or item.get("scope_match") is not True
            or item.get("run_id") != run_id
            or item.get("candidate_commit") != candidate.get("source_commit")
            or item.get("candidate_tree") != candidate.get("source_tree")
            or sorted(item.get("artifact_digests", []))
            != sorted(manifest.get("candidate_artifact_digests", []))
            or item.get("deployment_id") != deployment.get("deployment_id")
            or item.get("environment_id") != environment.get("environment_id")
            or item.get("account_id") != account.get("account_id")
            or sorted(item.get("resource_ids", []))
            != sorted(environment.get("resource_ids", []))
            or item.get("evidence_id") != f"{run_id}:{dependency_id}"
            or _evidence_ref_error(item.get("evidence_ref"))
        ):
            blockers.append("DEPENDENCY_EVIDENCE_MISMATCH")
    return sorted(set(blockers))
