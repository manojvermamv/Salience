from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
CURRENT_TIME = NOW.isoformat().replace("+00:00", "Z")
PAST_TIME = (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
FUTURE_TIME = (NOW + timedelta(days=30)).isoformat().replace("+00:00", "Z")
SOURCE_COMMIT = "a" * 40
SOURCE_TREE = "b" * 40
MIGRATION_HEAD = "0051_p1_recovery_rollback_v2"
P0_PATH = ROOT / "docs/v4/p0-release.json"
MATRIX_PATH = ROOT / "docs/v4/p1-qualification-matrix.json"

ROW_GATES = {
    "R01": "RG0",
    "R22": "RG0",
    "R69": "RG0",
    "R16F": "RG0",
    "R23F": "RG0",
    "R02": "RG1",
    "R03": "RG1",
    "R05": "RG1",
    "R06": "RG1",
    "R07": "RG1",
    "R08": "RG1",
    "R09": "RG1",
    "R10": "RG1",
    "R13": "RG1",
    "R15": "RG1",
    "R17": "RG1",
    "R18": "RG1",
    "R32": "RG1",
    "R33": "RG1",
    "R34": "RG1",
    "R35": "RG1",
    "R53": "RG1",
}
ROW_DEPENDENCIES = {
    "R02": ["P0", "R01"],
    "R03": ["P0", "R01"],
    "R05": ["P0", "R01"],
    "R06": ["R03", "R05"],
    "R07": ["R05", "R06"],
    "R08": ["R07"],
    "R09": ["R07", "R08"],
    "R10": ["R07"],
    "R13": ["R10"],
    "R15": ["P0", "R05"],
    "R17": ["R03", "R15"],
    "R18": ["R17"],
    "R32": ["R03", "R15"],
    "R33": ["R17", "R32"],
    "R34": ["R32", "R33"],
    "R35": ["R32"],
    "R53": ["P0", "R03"],
}
ROW_IDS = list(ROW_GATES)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _sha_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )


def _ref(label: str) -> dict[str, str]:
    digest = _sha_bytes(label.encode())
    return {
        "uri": f"artifact://sha256/{digest}",
        "sha256": digest,
        "retention_mode": "CONTENT_ADDRESSED",
    }


def _person(person_id: str, name: str, role: str) -> dict[str, Any]:
    return {
        "person_id": person_id,
        "name": name,
        "role": role,
        "identity_source": "approved-idp:salience.example",
        "verified_identity": True,
        "scope": ["environment:prod-synthetic", "account:account-synthetic"],
        "authorization_ref": _ref(f"authorization:{person_id}"),
        "valid_from": "2026-10-01T00:00:00Z",
        "valid_until": "2026-12-01T00:00:00Z",
    }


def _row_scope(route_id: str) -> dict[str, Any]:
    return {
        "candidate": {
            "source_commit": SOURCE_COMMIT,
            "source_tree": SOURCE_TREE,
            "artifact_digests": [
                _sha_bytes(f"artifact:{kind}:{name}".encode())
                for kind, name in [
                    ("OCI_IMAGE", "api"),
                    ("WHEEL", "salience"),
                    ("LAUNCHER", "worker-launcher"),
                    ("CONFIG_BUNDLE", "production-config"),
                    ("SOURCE_LOCK", "source-lock"),
                    ("ARTIFACT_LOCK", "artifact-lock"),
                ]
            ],
            "migration_head": MIGRATION_HEAD,
        },
        "deployment": {
            "deployment_id": "deploy-synthetic-001",
            "database_cluster_alias": "db-synthetic",
            "database_schema_head": MIGRATION_HEAD,
            "temporal_target_alias": "temporal-synthetic",
            "temporal_namespace": "salience-synthetic",
        },
        "environment": {
            "environment_id": "env-synthetic-001",
            "stage": "production-synthetic",
            "region": "us-test-1",
            "tenant_id": "tenant-synthetic",
            "workspace_id": "workspace-synthetic",
            "data_jurisdiction": ["US"],
        },
        "account": {
            "provider": "internal-synthetic",
            "account_id": "account-synthetic",
            "repository_id": "repo-synthetic",
        },
        "resource_ids": ["resource-synthetic-001"],
        "route_evidence_required": True,
        "route": {
            "route_id": route_id,
            "route_tuple_sha256": "2" * 64,
            "authenticated_route_manifest_sha256": "3" * 64,
        },
    }


def _row(row_id: str, run_id: str, route_id: str) -> dict[str, Any]:
    owner = _person("owner-01", "Morgan Lee", "accountable-row-owner")
    operator = _person("operator-01", "Avery Kim", "authorized-operator")
    reviewer = _person("reviewer-01", "Riley Patel", "independent-reviewer")
    dependencies = ROW_DEPENDENCIES.get(row_id, [])
    dependency_evidence = [
        {
            "dependency_id": dependency_id,
            "status": "PASS",
            "evidence_id": f"{run_id}:{dependency_id}",
            "candidate_commit": SOURCE_COMMIT,
            "deployment_id": "deploy-synthetic-001",
            "environment_id": "env-synthetic-001",
            "scope_match": True,
            "evidence_ref": _ref(f"dependency:{row_id}:{dependency_id}"),
        }
        for dependency_id in dependencies
    ]
    state_digest = "4" * 64
    return {
        "run_id": run_id,
        "row_id": row_id,
        "gate": ROW_GATES[row_id],
        "dependencies": dependencies,
        "dependency_evidence": dependency_evidence,
        "evidence_class": "EXTERNAL_PRODUCTION_ACCEPTANCE",
        "scope": _row_scope(route_id),
        "owner": owner,
        "operator": operator,
        "independent_reviewer": reviewer,
        "procedure": {
            "runbook": _ref(f"runbook:{row_id}"),
            "runbook_version": "v1.0.0",
            "executable": {
                "kind": "CLI",
                "executable": "/usr/bin/python3",
                "version": "3.13.9",
                "argv": ["python3", "-m", "acceptance.synthetic-check", row_id],
            },
            "request_sha256": "5" * 64,
            "principal": "operator-01",
            "expected_predicate": "recorded state matches the approved row predicate",
            "started_at": PAST_TIME,
            "completed_at": CURRENT_TIME,
            "command_ids": [f"command-{row_id.lower()}-001"],
            "request_ids": [],
            "trace_ids": [f"trace-{row_id.lower()}-001"],
        },
        "raw_result": _ref(f"raw-result:{row_id}"),
        "readback": _ref(f"readback:{row_id}"),
        "normalized_checks": [
            {
                "check_id": f"{row_id}:primary",
                "status": "PASS",
                "scope_match": True,
                "raw_result": _ref(f"check-result:{row_id}"),
            }
        ],
        "status": "PASS",
        "scope_match": True,
        "freshness": {
            "observation_class": "worker-route",
            "observed_at": PAST_TIME,
            "max_age_seconds": 3600,
            "policy": {
                "version": "ops-policy-2026-10",
                "approved": True,
                "approved_at": "2026-10-01T00:00:00Z",
                "expires_at": FUTURE_TIME,
                "evidence_ref": _ref(f"freshness-policy:{row_id}"),
            },
            "state": "CURRENT",
            "source_state_sha256": state_digest,
            "final_reviewed_state_sha256": state_digest,
        },
        "transition": {
            "mode": "NO_MUTATION",
            "pre_state_sha256": state_digest,
            "post_state_sha256": state_digest,
            "pre_state_readback": _ref(f"pre-state:{row_id}"),
            "post_state_readback": _ref(f"post-state:{row_id}"),
            "mutation_ids": [],
            "owner_acknowledged": True,
            "reviewer_acknowledged": True,
            "rollback": {
                "status": "VERIFIED_NO_MUTATION",
                "procedure": _ref(f"rollback-procedure:{row_id}"),
                "pre_state": _ref(f"rollback-pre-state:{row_id}"),
                "action_receipt": None,
                "post_state": _ref(f"rollback-post-state:{row_id}"),
                "post_state_sha256": state_digest,
                "unresolved_liabilities": [],
                "owner_acknowledged": True,
                "reviewer_acknowledged": True,
            },
        },
        "negative_control": {
            "required": False,
            "expected_outcome": None,
            "observed_outcome": None,
            "raw_denial": None,
            "independent_effect_readback": None,
            "observed_call_count": 0,
            "observed_effect_count": 0,
        },
        "credential_state": {
            "revoked_version_ids": [],
            "current_version_id": "credential-v2",
            "revocation_still_effective": True,
            "restored_version_ids": [],
        },
        "observability": {
            "correlation": {
                "run_id": run_id,
                "candidate_commit": SOURCE_COMMIT,
                "environment_id": "env-synthetic-001",
                "account_alias": "account-synthetic",
                "command_ids": [f"command-{row_id.lower()}-001"],
                "request_ids": [],
                "trace_ids": [f"trace-{row_id.lower()}-001"],
                "route_id": route_id,
                "schedule_ids": [f"schedule-{row_id.lower()}-001"],
                "operation_ids": [f"operation-{row_id.lower()}-001"],
                "external_receipt_ids": [f"receipt-{row_id.lower()}-001"],
                "timestamps_utc": [PAST_TIME, CURRENT_TIME],
            },
            "events": [_ref(f"event:{row_id}")],
            "metrics": [_ref(f"metrics:{row_id}")],
            "alert_delivery": _ref(f"alert-delivery:{row_id}"),
            "human_acknowledgement": _ref(f"human-ack:{row_id}"),
            "secret_free": True,
            "secret_scan": {
                "status": "PASS",
                "findings": [],
                "evidence_ref": _ref(f"secret-scan:{row_id}"),
            },
        },
        "operator_declaration": {
            "name": operator["name"],
            "status": "PASS",
            "signed_at": CURRENT_TIME,
            "evidence_ref": _ref(f"operator-declaration:{row_id}"),
        },
        "reviewer_decision": {
            "name": reviewer["name"],
            "status": "PASS",
            "reviewed_at": CURRENT_TIME,
            "evidence_ref": _ref(f"reviewer-decision:{row_id}"),
        },
        "reason": "Synthetic local structure fixture; no external procedure ran.",
    }


def _collect_refs(value: Any) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    if isinstance(value, dict):
        if {"uri", "sha256", "retention_mode"}.issubset(value):
            key = (value["uri"], value["sha256"])
            found[key] = {
                "uri": value["uri"],
                "sha256": value["sha256"],
                "retention_mode": value["retention_mode"],
            }
        for child in value.values():
            for ref in _collect_refs(child):
                found[(ref["uri"], ref["sha256"])] = ref
    elif isinstance(value, list):
        for child in value:
            for ref in _collect_refs(child):
                found[(ref["uri"], ref["sha256"])] = ref
    return [found[key] for key in sorted(found)]


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["manifest_sha256"] = _digest(payload["manifest"])
    payload["manifest_signature"]["signed_manifest_sha256"] = payload["manifest_sha256"]
    for row in payload["rows"]:
        row.pop("record_sha256", None)
        row["record_sha256"] = _digest(row)
    refs_source = {
        "manifest": payload["manifest"],
        "manifest_signature": payload["manifest_signature"],
        "rows": payload["rows"],
        "invalidation_records": payload["invalidation_records"],
        "final_audit": payload.get("final_audit"),
    }
    payload["artifact_digest_index"] = _collect_refs(refs_source)
    payload["package_digest"] = _digest(
        {
            "run_id": payload["run_id"],
            "manifest_sha256": payload["manifest_sha256"],
            "manifest_signature": payload["manifest_signature"],
            "artifact_digest_index": payload["artifact_digest_index"],
            "row_record_sha256": [row["record_sha256"] for row in payload["rows"]],
            "invalidation_records": payload["invalidation_records"],
        }
    )
    if payload.get("final_audit") is not None:
        audit = payload["final_audit"]
        audit.pop("record_sha256", None)
        audit["package_digest"] = payload["package_digest"]
        audit["record_sha256"] = _digest(audit)
    return payload


def _valid_package() -> dict[str, Any]:
    run_id = "run-synthetic-2026-10-28-001"
    artifacts = []
    for kind, name in [
        ("OCI_IMAGE", "api"),
        ("WHEEL", "salience"),
        ("LAUNCHER", "worker-launcher"),
        ("CONFIG_BUNDLE", "production-config"),
        ("SOURCE_LOCK", "source-lock"),
        ("ARTIFACT_LOCK", "artifact-lock"),
    ]:
        evidence = _ref(f"artifact:{kind}:{name}")
        artifacts.append(
            {
                "artifact_id": f"{kind.lower()}-{name}",
                "kind": kind,
                "name": name,
                "sha256": evidence["sha256"],
                "evidence_ref": evidence,
            }
        )
    artifact_ids = [item["artifact_id"] for item in artifacts]
    artifact_digests = [item["sha256"] for item in artifacts]
    manifest = {
        "package_scope": "SYNTHETIC",
        "created_at": CURRENT_TIME,
        "candidate": {
            "source_commit": SOURCE_COMMIT,
            "source_tree": SOURCE_TREE,
            "signed_build_provenance": _ref("candidate-build-provenance"),
            "artifact_lock": _ref("candidate-artifact-lock"),
            "artifacts": artifacts,
            "artifact_mapping": [
                {"component": item["name"], "artifact_id": item["artifact_id"]}
                for item in artifacts
            ],
            "migration_head": MIGRATION_HEAD,
        },
        "deployment": {
            "deployment_id": "deploy-synthetic-001",
            "services": [
                {"service_id": "api-synthetic", "artifact_id": artifact_ids[0]},
                {"service_id": "worker-synthetic", "artifact_id": artifact_ids[1]},
            ],
            "workers": [
                {"worker_id": "worker-synthetic", "artifact_id": artifact_ids[1], "route_ids": []}
            ],
            "database": {
                "cluster_alias": "db-synthetic",
                "schema_head": MIGRATION_HEAD,
            },
            "temporal": {
                "target_alias": "temporal-synthetic",
                "namespace": "salience-synthetic",
            },
            "storage_config_revision": "storage-synthetic-v1",
            "telemetry_config_revision": "telemetry-synthetic-v1",
            "started_at": PAST_TIME,
            "completed_at": CURRENT_TIME,
            "start_readback": _ref("deployment-start-readback"),
            "completion_readback": _ref("deployment-completion-readback"),
        },
        "environment": {
            "environment_id": "env-synthetic-001",
            "stage": "production-synthetic",
            "region": "us-test-1",
            "tenant_id": "tenant-synthetic",
            "workspace_id": "workspace-synthetic",
            "account_identity": {
                "provider": "internal-synthetic",
                "account_id": "account-synthetic",
                "repository_id": "repo-synthetic",
            },
            "resource_ids": ["resource-synthetic-001"],
            "data_jurisdiction": ["US"],
            "environment_manifest": _ref("environment-manifest"),
        },
        "effect_state": {
            "production_effects_enabled": False,
            "publication_budget": 0,
            "no_effect_sink": {
                "name": "synthetic-sink",
                "account_id": "account-synthetic",
                "evidence_ref": _ref("no-effect-sink"),
            },
        },
        "route_manifest": {
            "authenticated_manifest_sha256": "3" * 64,
            "evidence_ref": _ref("authenticated-route-manifest"),
        },
        "candidate_artifact_digests": artifact_digests,
    }
    rows = []
    for index, row_id in enumerate(ROW_IDS, start=1):
        route_id = f"00000000-0000-4000-8000-{index:012d}"
        rows.append(_row(row_id, run_id, route_id))
    final_audit = {
        "audit_id": "audit-synthetic-001",
        "record_type": "SEPARATE_FINAL_AUDIT",
        "status": "NOT RUN",
        "auditor": _person("auditor-01", "Jordan Rivera", "independent-final-auditor"),
        "audited_at": CURRENT_TIME,
        "rechecked_row_ids": ROW_IDS.copy(),
        "row_rechecks": [
            {
                "row_id": row_id,
                "status": "NOT RUN",
                "readback": _ref(f"final-audit-readback:{row_id}"),
            }
            for row_id in ROW_IDS
        ],
        "p0_release_readback": _ref("final-audit-p0-readback"),
        "p1_matrix_readback": _ref("final-audit-p1-matrix-readback"),
        "disposition": "NO_GO",
        "evidence_ref": _ref("final-audit-record"),
    }
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "manifest": manifest,
        "manifest_sha256": "",
        "manifest_signature": {
            "signer_name": "Salience Evidence Signer",
            "key_id": "acceptance-key-synthetic-v1",
            "algorithm": "Ed25519",
            "signed_manifest_sha256": "",
            "signature_evidence": _ref("manifest-signature"),
        },
        "rows": rows,
        "invalidation_records": [],
        "final_audit": final_audit,
        "artifact_digest_index": [],
        "package_digest": "",
        "application_migrations": [],
        "later_phase_claims": [],
        "gate_mutation": False,
    }
    return _seal(payload)


def _validate(payload: dict[str, Any]):
    from salience.acceptance.p1_external_evidence import validate_acceptance_package

    return validate_acceptance_package(payload, now=NOW)


def _row_by_id(payload: dict[str, Any], row_id: str) -> dict[str, Any]:
    return next(row for row in payload["rows"] if row["row_id"] == row_id)


def _result_row(result: Any, row_id: str) -> dict[str, Any]:
    return result.to_dict()["rows"][row_id]


def _blocker_codes(result: Any) -> set[str]:
    return {item["code"] for item in result.to_dict()["blockers"]}


def test_package_requires_exact_candidate_tree_artifacts_deployment_and_environment() -> None:
    valid = _validate(_valid_package())
    assert valid.to_dict()["structural_status"] == "VALID"

    mutations = []
    package = _valid_package()
    del package["manifest"]["candidate"]["source_tree"]
    mutations.append(package)
    package = _valid_package()
    package["manifest"]["candidate"]["source_commit"] = "a" * 7
    mutations.append(package)
    package = _valid_package()
    package["manifest"]["candidate"]["artifacts"][0]["sha256"] = "f" * 64
    mutations.append(package)
    package = _valid_package()
    del package["manifest"]["deployment"]["deployment_id"]
    mutations.append(package)
    package = _valid_package()
    del package["manifest"]["environment"]["environment_id"]
    mutations.append(package)
    package = _valid_package()
    package["manifest"]["candidate"]["migration_head"] = ""
    mutations.append(package)
    package = _valid_package()
    package["manifest"]["candidate"]["artifacts"] = None
    mutations.append(package)

    for invalid in mutations:
        _seal(invalid)
        result = _validate(invalid)
        assert result.to_dict()["structural_status"] == "INVALID"


def test_package_requires_exact_row_set_once() -> None:
    for change in ("missing", "duplicate", "unknown", "wrong_gate"):
        package = _valid_package()
        if change == "missing":
            package["rows"].pop()
        elif change == "duplicate":
            package["rows"][-1] = copy.deepcopy(package["rows"][0])
        elif change == "unknown":
            package["rows"][-1]["row_id"] = "R99"
        else:
            _row_by_id(package, "R01")["gate"] = "RG1"
        _seal(package)
        result = _validate(package)
        assert result.to_dict()["structural_status"] == "INVALID"
        assert result.to_dict()["disposition"] == "NO_GO"


def test_each_row_requires_named_authorized_owner_operator_and_independent_reviewer() -> None:
    for change in ("unnamed", "identity_collision", "expired_authority", "unverified", "non_utc_authority"):
        package = _valid_package()
        row = _row_by_id(package, "R22")
        if change == "unnamed":
            row["owner"]["name"] = ""
        elif change == "identity_collision":
            row["independent_reviewer"]["person_id"] = row["operator"]["person_id"]
        elif change == "expired_authority":
            row["operator"]["valid_until"] = "2026-10-01T00:00:00Z"
        elif change == "non_utc_authority":
            row["operator"]["valid_from"] = "2026-10-01T01:00:00+01:00"
        else:
            row["owner"]["verified_identity"] = False
        _seal(package)
        result = _validate(package)
        assert result.to_dict()["disposition"] == "NO_GO"
        assert _result_row(result, "R22")["local_validation"] != "VALID"


def test_procedure_is_pinned_and_executable() -> None:
    for change in ("runbook_digest", "argv", "version", "command_id"):
        package = _valid_package()
        procedure = _row_by_id(package, "R01")["procedure"]
        if change == "runbook_digest":
            procedure["runbook"]["sha256"] = "0" * 64
        elif change == "argv":
            procedure["executable"]["argv"] = []
        elif change == "version":
            procedure["executable"]["version"] = ""
        else:
            procedure["command_ids"] = []
        _seal(package)
        result = _validate(package)
        assert result.to_dict()["disposition"] == "NO_GO"
        assert _result_row(result, "R01")["effective_status"] != "PASS"


def test_raw_results_and_rollback_are_hashed_and_referenced() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R08")
    row["transition"]["mode"] = "PLANNED_TRANSITION"
    row["transition"].update(
        {
            "change_ticket": "CHG-SYNTHETIC-001",
            "target_scope": "account-synthetic/resource-synthetic-001",
            "expected_pre_version": "route-v1",
            "expected_post_version": "route-v2",
            "observed_pre_version": "route-v1",
            "observed_post_version": "route-v2",
            "actor": "operator-01",
            "command_receipt": _ref("planned-command-receipt"),
            "mutation_ids": ["mutation-synthetic-001"],
        }
    )
    row["transition"]["rollback"].update(
        {
            "status": "VERIFIED",
            "action_receipt": _ref("rollback-action-receipt"),
            "post_state_sha256": "6" * 64,
        }
    )
    _seal(package)
    valid = _validate(package)
    assert valid.to_dict()["structural_status"] == "VALID"

    package = _valid_package()
    digest = _sha_bytes(b"immutable WORM raw result")
    _row_by_id(package, "R08")["raw_result"] = {
        "uri": "https://evidence.salience.example/objects/raw-result-001",
        "sha256": digest,
        "retention_mode": "WORM_OBJECT_VERSION",
        "object_version": "version-001",
    }
    _seal(package)
    assert _validate(package).to_dict()["structural_status"] == "VALID"

    invalid = copy.deepcopy(package)
    _row_by_id(invalid, "R08")["raw_result"]["sha256"] = "0" * 64
    _seal(invalid)
    result = _validate(invalid)
    assert result.to_dict()["disposition"] == "NO_GO"
    assert _result_row(result, "R08")["effective_status"] != "PASS"

    invalid = copy.deepcopy(package)
    del _row_by_id(invalid, "R08")["transition"]["rollback"]["post_state"]
    _seal(invalid)
    result = _validate(invalid)
    assert result.to_dict()["disposition"] == "NO_GO"


def test_scope_transfer_across_fixture_ci_commit_environment_account_is_rejected() -> None:
    for change in ("fixture", "ci", "commit", "environment", "account"):
        package = _valid_package()
        row = _row_by_id(package, "R03")
        if change == "fixture":
            row["evidence_class"] = "LOCAL_FIXTURE"
        elif change == "ci":
            row["evidence_class"] = "CI"
        elif change == "commit":
            row["scope"]["candidate"]["source_commit"] = "9" * 40
        elif change == "environment":
            row["scope"]["environment"]["environment_id"] = "other-environment"
        else:
            row["scope"]["account"]["account_id"] = "other-account"
        _seal(package)
        result = _validate(package)
        assert result.to_dict()["disposition"] == "NO_GO"
        assert _result_row(result, "R03")["effective_status"] != "PASS"


def test_ci_formal_approval_and_external_acceptance_remain_distinct() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R01")
    row["evidence_class"] = "CI"
    row["formal_approval"] = {
        "status": "PASS",
        "reviewer": "Riley Patel",
        "evidence_ref": _ref("formal-approval"),
    }
    _seal(package)
    result = _validate(package)
    row_result = _result_row(result, "R01")
    assert result.to_dict()["disposition"] == "NO_GO"
    assert row_result["effective_status"] != "PASS"
    assert "EVIDENCE_CLASS_NOT_EXTERNAL" in row_result["blockers"]


def test_fail_not_run_held_stale_or_scope_mismatch_blocks_package() -> None:
    for change, claimed_status in [
        ("status", "FAIL"),
        ("status", "NOT RUN"),
        ("status", "HELD"),
        ("freshness", "STALE"),
        ("scope", False),
    ]:
        package = _valid_package()
        row = _row_by_id(package, "R06")
        if change == "status":
            row["status"] = claimed_status
        elif change == "freshness":
            row["freshness"]["state"] = claimed_status
        else:
            row["scope_match"] = claimed_status
        _seal(package)
        result = _validate(package)
        assert result.to_dict()["disposition"] == "NO_GO"
        assert _result_row(result, "R06")["effective_status"] != "PASS"

    package = _valid_package()
    _row_by_id(package, "R03")["scope_match"] = False
    _seal(package)
    result = _validate(package)
    assert _result_row(result, "R03")["effective_status"] != "PASS"
    assert _result_row(result, "R17")["effective_status"] != "PASS"
    assert "DEPENDENCY_NOT_PASS" in _result_row(result, "R17")["blockers"]


def test_missing_or_unapproved_freshness_limit_holds_mutable_evidence() -> None:
    for change in ("missing_limit", "unapproved", "expired"):
        package = _valid_package()
        freshness = _row_by_id(package, "R05")["freshness"]
        if change == "missing_limit":
            freshness["max_age_seconds"] = None
        elif change == "unapproved":
            freshness["policy"]["approved"] = False
        else:
            freshness["policy"]["expires_at"] = "2026-10-01T00:00:00Z"
        _seal(package)
        result = _validate(package)
        row_result = _result_row(result, "R05")
        assert result.to_dict()["disposition"] == "NO_GO"
        assert row_result["effective_status"] == "HELD"
        assert "FRESHNESS_POLICY_INVALID" in row_result["blockers"]


def test_planned_revocation_with_verified_final_denial_passes() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R22")
    row["transition"].update(
        {
            "mode": "PLANNED_TRANSITION",
            "change_ticket": "CHG-REVOKE-SYNTHETIC",
            "target_scope": "identity:synthetic-credential",
            "expected_pre_version": "credential-v1",
            "expected_post_version": "credential-v2",
            "observed_pre_version": "credential-v1",
            "observed_post_version": "credential-v2",
            "actor": "operator-01",
            "command_receipt": _ref("credential-revocation-command"),
            "pre_state_sha256": "7" * 64,
            "post_state_sha256": "8" * 64,
            "mutation_ids": ["credential-revocation-001"],
        }
    )
    row["transition"]["rollback"].update(
        {
            "status": "VERIFIED_FINAL_REVOKED_STATE",
            "action_receipt": _ref("credential-final-state-readback"),
            "post_state_sha256": "8" * 64,
        }
    )
    row["freshness"]["source_state_sha256"] = "8" * 64
    row["freshness"]["final_reviewed_state_sha256"] = "8" * 64
    row["credential_state"] = {
        "revoked_version_ids": ["credential-v1"],
        "current_version_id": "credential-v2",
        "revocation_still_effective": True,
        "restored_version_ids": [],
    }
    row["negative_control"] = {
        "required": True,
        "expected_outcome": "DENIED",
        "observed_outcome": "DENIED",
        "raw_denial": _ref("credential-denial-raw-result"),
        "independent_effect_readback": _ref("credential-zero-effect-readback"),
        "observed_call_count": 0,
        "observed_effect_count": 0,
    }
    _seal(package)
    result = _validate(package)
    row_result = _result_row(result, "R22")
    assert result.to_dict()["structural_status"] == "VALID"
    assert row_result["local_validation"] == "VALID"
    assert row_result["effective_status"] == "HELD"
    assert "STALE" not in row_result["blockers"]
    assert result.to_dict()["disposition"] == "NO_GO"


def test_unplanned_or_subsequent_revision_change_fails() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R08")
    row["transition"].update(
        {
            "mode": "PLANNED_TRANSITION",
            "change_ticket": "CHG-ROUTE-SYNTHETIC",
            "target_scope": "route:synthetic",
            "expected_pre_version": "route-v1",
            "expected_post_version": "route-v2",
            "observed_pre_version": "route-v1",
            "observed_post_version": "route-v2",
            "actor": "operator-01",
            "command_receipt": _ref("route-transition-command"),
            "pre_state_sha256": "7" * 64,
            "post_state_sha256": "8" * 64,
            "mutation_ids": ["route-mutation-001"],
            "subsequent_revision": "route-v3",
            "final_reviewed_version": "route-v2",
        }
    )
    _seal(package)
    result = _validate(package)
    row_result = _result_row(result, "R08")
    assert result.to_dict()["disposition"] == "NO_GO"
    assert row_result["effective_status"] == "HELD"
    assert "UNPLANNED_REVISION_DRIFT" in row_result["blockers"]


def test_no_mutation_requires_equal_start_and_end_state() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R01")
    row["transition"]["post_state_sha256"] = "9" * 64
    _seal(package)
    result = _validate(package)
    row_result = _result_row(result, "R01")
    assert result.to_dict()["disposition"] == "NO_GO"
    assert row_result["effective_status"] == "FAIL"
    assert "NO_MUTATION_STATE_MISMATCH" in row_result["blockers"]


def test_revoked_credentials_are_never_restored() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R22")
    row["credential_state"] = {
        "revoked_version_ids": ["credential-v1"],
        "current_version_id": "credential-v1",
        "revocation_still_effective": False,
        "restored_version_ids": ["credential-v1"],
    }
    _seal(package)
    result = _validate(package)
    row_result = _result_row(result, "R22")
    assert result.to_dict()["disposition"] == "NO_GO"
    assert row_result["effective_status"] == "HELD"
    assert "REVOKED_CREDENTIAL_RESTORED" in row_result["blockers"]


def test_run_invalidation_is_append_only_and_transitive() -> None:
    package = _valid_package()
    package["invalidation_records"] = [
        {
            "invalidation_id": "invalidation-001",
            "sequence": 1,
            "source_run_id": "run-synthetic-2026-10-27-001",
            "source_package_sha256": "a" * 64,
            "new_run_id": package["run_id"],
            "scope": "ROW",
            "source_row_id": "R03",
            "affected_row_ids": ["R03"],
            "reason": "route revision drift",
            "invalidated_at": CURRENT_TIME,
        }
    ]
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"
    assert result.to_dict()["structural_status"] == "INVALID"
    assert "INVALIDATION_CLOSURE_MISMATCH" in _blocker_codes(result)

    package = _valid_package()
    package["invalidation_records"] = [
        {
            "invalidation_id": "invalidation-001",
            "sequence": 1,
            "source_run_id": "run-synthetic-2026-10-27-001",
            "source_package_sha256": "a" * 64,
            "new_run_id": package["run_id"],
            "scope": "ROW",
            "source_row_id": "R03",
            "affected_row_ids": [
                "R03", "R06", "R07", "R08", "R09", "R10", "R13",
                "R17", "R18", "R32", "R33", "R34", "R35", "R53",
            ],
            "reason": "route revision drift",
            "invalidated_at": CURRENT_TIME,
        }
    ]
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["structural_status"] == "VALID"
    assert _result_row(result, "R03")["effective_status"] == "HELD"
    assert _result_row(result, "R34")["effective_status"] == "HELD"


def test_negative_controls_pass_only_on_proven_denial_and_zero_effect() -> None:
    for row_id in ("R22", "R16F", "R23F"):
        package = _valid_package()
        row = _row_by_id(package, row_id)
        row["negative_control"]["required"] = False
        _seal(package)
        result = _validate(package)
        assert _result_row(result, row_id)["effective_status"] != "PASS"
        assert "NEGATIVE_CONTROL_REQUIRED" in _result_row(result, row_id)["blockers"]

    package = _valid_package()
    row = _row_by_id(package, "R23F")
    row["negative_control"] = {
        "required": True,
        "expected_outcome": "DENIED",
        "observed_outcome": "DENIED",
        "raw_denial": _ref("negative-control-denial"),
        "independent_effect_readback": _ref("negative-control-zero-effect"),
        "observed_call_count": 0,
        "observed_effect_count": 0,
    }
    _seal(package)
    valid = _validate(package)
    assert _result_row(valid, "R23F")["local_validation"] == "VALID"

    package = _valid_package()
    row = _row_by_id(package, "R23F")
    row["negative_control"] = {
        "required": True,
        "expected_outcome": "DENIED",
        "observed_outcome": "DENIED",
        "raw_denial": _ref("negative-control-denial"),
        "independent_effect_readback": _ref("negative-control-effect-count-one"),
        "observed_call_count": 1,
        "observed_effect_count": 1,
    }
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"
    assert "NEGATIVE_CONTROL_EFFECT_OBSERVED" in _result_row(result, "R23F")["blockers"]


def test_package_cannot_enable_production_or_release_a_gate() -> None:
    invalid_cli = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-p1-external-acceptance.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid_cli.returncode == 2
    assert json.loads(invalid_cli.stdout)["disposition"] == "NO_GO"

    package = _valid_package()
    package["manifest"]["effect_state"]["production_effects_enabled"] = True
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"
    assert "PRODUCTION_EFFECTS_MUST_REMAIN_DISABLED" in _blocker_codes(result)

    package = _valid_package()
    package["gate_mutation"] = True
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"

    package = _valid_package()
    package_path = ROOT / "tests/unit/.tmp-p1-external-package.json"
    schema_path = ROOT / "docs/v4/p1-rg0-rg1-external-acceptance.schema.json"
    before_p0 = P0_PATH.read_bytes()
    before_matrix = MATRIX_PATH.read_bytes()
    package_path.write_text(json.dumps(package), encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/check-p1-external-acceptance.py"),
                "--package",
                str(package_path),
                "--schema",
                str(schema_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.stdout.startswith("{")
        cli_result = json.loads(completed.stdout)
        assert completed.returncode != 0
        assert cli_result["structural_status"] == "VALID"
        assert cli_result["disposition"] == "NO_GO"
        assert "GO" not in cli_result.get("gate_state", "")
    finally:
        package_path.unlink(missing_ok=True)
    assert P0_PATH.read_bytes() == before_p0
    assert MATRIX_PATH.read_bytes() == before_matrix


def test_final_audit_is_separate_and_rechecks_every_row() -> None:
    package = _valid_package()
    result = _validate(package)
    assert result.to_dict()["structural_status"] == "VALID"
    assert package["final_audit"]["record_type"] == "SEPARATE_FINAL_AUDIT"
    assert package["final_audit"]["package_digest"] == package["package_digest"]
    assert package["final_audit"]["rechecked_row_ids"] == ROW_IDS
    assert {item["row_id"] for item in package["final_audit"]["row_rechecks"]} == set(ROW_IDS)
    assert package["final_audit"]["status"] == "NOT RUN"
    assert result.to_dict()["disposition"] == "NO_GO"

    package = _valid_package()
    package["final_audit"]["status"] = "PASS"
    package["final_audit"]["disposition"] = "READY_FOR_FINAL_AUDIT"
    for recheck in package["final_audit"]["row_rechecks"]:
        recheck["status"] = "NOT RUN"
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"
    assert "FINAL_AUDIT_ROW_RECHECK_INCOMPLETE" in _blocker_codes(result)


def test_rollback_requires_pre_state_action_and_exact_post_state_readback() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R08")
    row["transition"].update(
        {
            "mode": "PLANNED_TRANSITION",
            "change_ticket": "CHG-ROLLBACK-SYNTHETIC",
            "target_scope": "route:synthetic",
            "expected_pre_version": "route-v1",
            "expected_post_version": "route-v2",
            "observed_pre_version": "route-v1",
            "observed_post_version": "route-v2",
            "actor": "operator-01",
            "command_receipt": _ref("planned-mutation-receipt"),
            "mutation_ids": ["mutation-001"],
        }
    )
    row["transition"]["rollback"].update(
        {
            "status": "UNKNOWN",
            "action_receipt": None,
            "post_state_sha256": "9" * 64,
            "deployment_id": "other-deployment",
            "unresolved_liabilities": ["remote-operation-unknown"],
        }
    )
    _seal(package)
    result = _validate(package)
    row_result = _result_row(result, "R08")
    assert result.to_dict()["disposition"] == "NO_GO"
    assert row_result["effective_status"] == "HELD"
    assert "ROLLBACK_UNVERIFIED" in row_result["blockers"]


def test_observability_artifacts_are_correlated_and_secret_free() -> None:
    package = _valid_package()
    result = _validate(package)
    assert result.to_dict()["structural_status"] == "VALID"

    package = _valid_package()
    row = _row_by_id(package, "R34")
    row["observability"]["correlation"]["run_id"] = "other-run"
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"
    assert "OBSERVABILITY_CORRELATION_MISMATCH" in _result_row(result, "R34")["blockers"]

    package = _valid_package()
    row = _row_by_id(package, "R34")
    row["observability"]["secret_payload"] = "Bearer super-secret-token-value"
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"
    assert "SECRET_FOUND_IN_EVIDENCE" in _result_row(result, "R34")["blockers"]


def test_default_package_adds_no_application_migration_and_excludes_later_phases() -> None:
    before_p0 = _sha_bytes(P0_PATH.read_bytes())
    before_matrix = _sha_bytes(MATRIX_PATH.read_bytes())
    package = _valid_package()
    assert package["application_migrations"] == []
    assert package["manifest"]["candidate"]["migration_head"] == MIGRATION_HEAD
    assert package["later_phase_claims"] == []
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"

    package = _valid_package()
    package["later_phase_claims"] = ["P2_PROVIDER_EFFECTS", "FUTURE_RECORD_FAMILIES"]
    _seal(package)
    result = _validate(package)
    assert result.to_dict()["disposition"] == "NO_GO"
    assert result.to_dict()["structural_status"] == "INVALID"
    assert _sha_bytes(P0_PATH.read_bytes()) == before_p0
    assert _sha_bytes(MATRIX_PATH.read_bytes()) == before_matrix
