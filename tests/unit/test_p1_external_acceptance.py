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


def _route_binding(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "applicability": "ROUTE",
        "route_id": route["route_id"],
        "route_tuple_sha256": route["route_tuple_sha256"],
        "authenticated_route_manifest_sha256": route[
            "authenticated_route_manifest_sha256"
        ],
    }


def _route_set_binding(inventory: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "applicability": "ROUTE_SET",
        "routes": sorted(
            [
                {
                    "route_id": route["route_id"],
                    "route_tuple_sha256": route["route_tuple_sha256"],
                    "authenticated_route_manifest_sha256": route[
                        "authenticated_route_manifest_sha256"
                    ],
                }
                for route in inventory
            ],
            key=lambda item: item["route_id"],
        ),
    }


def _fixture_scope_axes() -> dict[str, Any]:
    return {
        "candidate_commit": SOURCE_COMMIT,
        "candidate_tree": SOURCE_TREE,
        "artifact_digests": sorted(
            [
                _sha_bytes(f"artifact:{kind}:{name}".encode())
                for kind, name in [
                    ("OCI_IMAGE", "api"),
                    ("WHEEL", "salience"),
                    ("LAUNCHER", "worker-launcher"),
                    ("CONFIG_BUNDLE", "production-config"),
                    ("SOURCE_LOCK", "source-lock"),
                    ("ARTIFACT_LOCK", "artifact-lock"),
                ]
            ]
        ),
        "migration_head": MIGRATION_HEAD,
        "deployment_id": "deploy-synthetic-001",
        "database_cluster_alias": "db-synthetic",
        "database_schema_head": MIGRATION_HEAD,
        "temporal_target_alias": "temporal-synthetic",
        "temporal_namespace": "salience-synthetic",
        "storage_config_revision": "storage-synthetic-v1",
        "telemetry_config_revision": "telemetry-synthetic-v1",
        "environment_id": "env-synthetic-001",
        "stage": "production-synthetic",
        "region": "us-test-1",
        "tenant_id": "tenant-synthetic",
        "workspace_id": "workspace-synthetic",
        "provider": "internal-synthetic",
        "account_id": "account-synthetic",
        "repository_id": "repo-synthetic",
        "resource_ids": ["resource-synthetic-001"],
        "data_jurisdiction": ["US"],
    }


def _authorization_scope(route_binding: dict[str, Any]) -> dict[str, Any]:
    return {**_fixture_scope_axes(), "route_binding": copy.deepcopy(route_binding)}


def _person(
    person_id: str,
    name: str,
    role: str,
    route_binding: dict[str, Any],
    reference_suffix: str,
) -> dict[str, Any]:
    return {
        "person_id": person_id,
        "name": name,
        "role": role,
        "identity_source": "approved-idp:salience.example",
        "verified_identity": True,
        "scope": _authorization_scope(route_binding),
        "authorization_ref": _ref(f"authorization:{person_id}:{reference_suffix}"),
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
            "storage_config_revision": "storage-synthetic-v1",
            "telemetry_config_revision": "telemetry-synthetic-v1",
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


def _fixture_evidence_binding(
    run_id: str,
    route_binding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        **_fixture_scope_axes(),
        "route_binding": copy.deepcopy(route_binding),
    }


def _row(row_id: str, run_id: str, route_id: str) -> dict[str, Any]:
    scope = _row_scope(route_id)
    route_binding = _route_binding(scope["route"])
    owner = _person("owner-01", "Morgan Lee", "accountable-row-owner", route_binding, row_id)
    operator = _person("operator-01", "Avery Kim", "authorized-operator", route_binding, row_id)
    reviewer = _person("reviewer-01", "Riley Patel", "independent-reviewer", route_binding, row_id)
    dependencies = ROW_DEPENDENCIES.get(row_id, [])
    dependency_evidence = [
        {
            "dependency_id": dependency_id,
            "status": "PASS",
            "evidence_id": f"{run_id}:{dependency_id}",
            "run_id": run_id,
            "candidate_commit": SOURCE_COMMIT,
            "candidate_tree": SOURCE_TREE,
            "artifact_digests": _fixture_evidence_binding(run_id, route_binding)["artifact_digests"],
            "deployment_id": "deploy-synthetic-001",
            "environment_id": "env-synthetic-001",
            "account_id": "account-synthetic",
            "resource_ids": ["resource-synthetic-001"],
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
        "scope": scope,
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
            "final_reviewed_version": "stable-v1",
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
                "scope": copy.deepcopy(scope),
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
                "candidate_tree": SOURCE_TREE,
            "artifact_digests": _fixture_evidence_binding(run_id, route_binding)["artifact_digests"],
                "deployment_id": "deploy-synthetic-001",
                "environment_id": "env-synthetic-001",
                "account_alias": "account-synthetic",
                "resource_ids": ["resource-synthetic-001"],
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


def _reference_paths(
    value: Any,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], dict[str, str]]]:
    references: list[tuple[tuple[str, ...], dict[str, str]]] = []
    if isinstance(value, dict):
        if {"uri", "sha256", "retention_mode"}.issubset(value):
            references.append((path, value))
        for key, child in value.items():
            references.extend(_reference_paths(child, (*path, str(key))))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(_reference_paths(child, (*path, str(index))))
    return references


def _bind_references(value: Any, context: dict[str, Any], operator_id: str) -> None:
    if isinstance(value, dict):
        if {"uri", "sha256", "retention_mode"}.issubset(value):
            digest = value.get("sha256")
            value.setdefault("evidence_id", f"evidence-{digest}")
            value.setdefault("binding", copy.deepcopy(context))
            value.setdefault(
                "provenance",
                {
                    "source_id": f"evidence-source-{digest}",
                    "operator_id": operator_id,
                },
            )
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                _bind_references(child, context, operator_id)
    elif isinstance(value, list):
        for child in value:
            _bind_references(child, context, operator_id)


def _binding_context(
    payload: dict[str, Any],
    route_binding: dict[str, Any],
) -> dict[str, Any]:
    manifest = payload["manifest"]
    candidate = manifest.get("candidate", {})
    environment = manifest.get("environment", {})
    account = environment.get("account_identity", {})
    deployment = manifest.get("deployment", {})
    database = deployment.get("database", {})
    temporal = deployment.get("temporal", {})
    artifact_digests = manifest.get("candidate_artifact_digests", [])
    resource_ids = environment.get("resource_ids", [])
    data_jurisdiction = environment.get("data_jurisdiction", [])
    return {
        "run_id": payload.get("run_id"),
        "candidate_commit": candidate.get("source_commit"),
        "candidate_tree": candidate.get("source_tree"),
        "artifact_digests": sorted(artifact_digests) if isinstance(artifact_digests, list) else artifact_digests,
        "migration_head": candidate.get("migration_head"),
        "deployment_id": deployment.get("deployment_id"),
        "database_cluster_alias": database.get("cluster_alias"),
        "database_schema_head": database.get("schema_head"),
        "temporal_target_alias": temporal.get("target_alias"),
        "temporal_namespace": temporal.get("namespace"),
        "storage_config_revision": deployment.get("storage_config_revision"),
        "telemetry_config_revision": deployment.get("telemetry_config_revision"),
        "environment_id": environment.get("environment_id"),
        "stage": environment.get("stage"),
        "region": environment.get("region"),
        "tenant_id": environment.get("tenant_id"),
        "workspace_id": environment.get("workspace_id"),
        "provider": account.get("provider"),
        "account_id": account.get("account_id"),
        "repository_id": account.get("repository_id"),
        "resource_ids": sorted(resource_ids) if isinstance(resource_ids, list) else resource_ids,
        "data_jurisdiction": sorted(data_jurisdiction) if isinstance(data_jurisdiction, list) else data_jurisdiction,
        "route_binding": copy.deepcopy(route_binding),
    }


def _bind_package_references(payload: dict[str, Any]) -> None:
    manifest = payload["manifest"]
    route_inventory = manifest["route_manifest"]["inventory"]
    route_set = _route_set_binding(route_inventory)
    all_routes_context = _binding_context(payload, route_set)
    for route in route_inventory:
        route_context = _binding_context(payload, _route_binding(route))
        route.setdefault("binding", copy.deepcopy(route_context))
        route["evidence_ref"].setdefault("binding", copy.deepcopy(route_context))
    for row in payload.get("rows", []):
        context = _binding_context(payload, _route_binding(row["scope"]["route"]))
        row.get("freshness", {}).setdefault("binding", copy.deepcopy(context))
        for dependency in row.get("dependency_evidence", []):
            dependency.setdefault("binding", copy.deepcopy(context))
        row.get("observability", {}).get("correlation", {}).setdefault(
            "binding", copy.deepcopy(context)
        )
    for record in payload.get("invalidation_records", []):
        source_row = next(
            (
                row
                for row in payload.get("rows", [])
                if row["row_id"] == record.get("source_row_id")
            ),
            None,
        )
        context = (
            _binding_context(payload, _route_binding(source_row["scope"]["route"]))
            if record.get("scope") == "ROW" and source_row is not None
            else all_routes_context
        )
        record.setdefault("binding", copy.deepcopy(context))

    _bind_references(manifest, all_routes_context, "deployment-authority")
    _bind_references(payload.get("manifest_signature", {}), all_routes_context, "evidence-signer")
    for route in route_inventory:
        route_context = _binding_context(payload, _route_binding(route))
        route.setdefault("binding", copy.deepcopy(route_context))
        route["evidence_ref"].setdefault("binding", copy.deepcopy(route_context))
    for row in payload.get("rows", []):
        context = _binding_context(payload, _route_binding(row["scope"]["route"]))
        operator_id = row.get("operator", {}).get("person_id", "operator-unknown")
        reviewer_id = row.get("independent_reviewer", {}).get("person_id", "reviewer-unknown")
        _bind_references(row, context, operator_id)
        for key in ("readback", "independent_effect_readback"):
            reference = row.get(key)
            if isinstance(reference, dict) and "provenance" in reference:
                reference["provenance"]["operator_id"] = reviewer_id
        control = row.get("negative_control", {})
        independent_readback = control.get("independent_effect_readback")
        if isinstance(independent_readback, dict) and "provenance" in independent_readback:
            independent_readback["provenance"]["operator_id"] = reviewer_id
    audit = payload.get("final_audit", {})
    auditor_id = audit.get("auditor", {}).get("person_id", "auditor-unknown")
    _bind_references(audit, all_routes_context, auditor_id)
    rows_by_id = {row["row_id"]: row for row in payload.get("rows", [])}
    for recheck in audit.get("row_rechecks", []):
        row = rows_by_id.get(recheck.get("row_id"))
        if row is None:
            continue
        route_context = _binding_context(payload, _route_binding(row["scope"]["route"]))
        recheck["readback"]["binding"] = copy.deepcopy(route_context)
        recheck["readback"].setdefault(
            "provenance",
            {"source_id": f"audit-source-{recheck['row_id']}", "operator_id": auditor_id},
        )
        recheck["readback"]["provenance"]["operator_id"] = auditor_id


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    _bind_package_references(payload)
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
            "lineage": payload.get("lineage"),
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
    route_ids = [f"00000000-0000-4000-8000-{index:012d}" for index in range(1, len(ROW_IDS) + 1)]
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
                {"service_id": "api-synthetic", "component": "api", "artifact_id": artifact_ids[0]},
            ],
            "workers": [
                {
                    "worker_id": "worker-synthetic",
                    "component": "salience",
                    "artifact_id": artifact_ids[1],
                    "route_ids": route_ids,
                }
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
            "inventory": [
                {
                    "route_id": route_id,
                    "route_tuple_sha256": "2" * 64,
                    "worker_id": "worker-synthetic",
                    "authenticated_route_manifest_sha256": "3" * 64,
                    "evidence_ref": _ref(f"authenticated-route:{route_id}"),
                }
                for route_id in route_ids
            ],
        },
        "candidate_artifact_digests": artifact_digests,
    }
    rows = []
    for route_id, row_id in zip(route_ids, ROW_IDS, strict=True):
        rows.append(_row(row_id, run_id, route_id))
    final_audit = {
        "audit_id": "audit-synthetic-001",
        "record_type": "SEPARATE_FINAL_AUDIT",
        "status": "NOT RUN",
        "auditor": _person(
            "auditor-01",
            "Jordan Rivera",
            "independent-final-auditor",
            _route_set_binding(manifest["route_manifest"]["inventory"]),
            "all-routes",
        ),
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
        "lineage": {"kind": "ROOT"},
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
    result_dict = result.to_dict()
    codes = {item["code"] for item in result_dict["blockers"]}
    for row in result_dict["rows"].values():
        codes.update(row["blockers"])
    return codes


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
            "target_scope": copy.deepcopy(row["scope"]),
            "expected_pre_version": "route-v1",
            "expected_post_version": "route-v2",
            "observed_pre_version": "route-v1",
            "observed_post_version": "route-v2",
            "actor": "operator-01",
            "command_receipt": _ref("planned-command-receipt"),
            "pre_state_sha256": "5" * 64,
            "post_state_sha256": "6" * 64,
            "mutation_ids": ["mutation-synthetic-001"],
            "final_reviewed_version": "route-v2",
        }
    )
    row["freshness"].update(
        {
            "source_state_sha256": "5" * 64,
            "final_reviewed_state_sha256": "6" * 64,
            "final_reviewed_version": "route-v2",
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
            "target_scope": copy.deepcopy(row["scope"]),
            "expected_pre_version": "credential-v1",
            "expected_post_version": "credential-v2",
            "observed_pre_version": "credential-v1",
            "observed_post_version": "credential-v2",
            "actor": "operator-01",
            "command_receipt": _ref("credential-revocation-command"),
            "pre_state_sha256": "7" * 64,
            "post_state_sha256": "8" * 64,
            "mutation_ids": ["credential-revocation-001"],
            "final_reviewed_version": "credential-v2",
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
    row["freshness"]["final_reviewed_version"] = "credential-v2"
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
            "target_scope": copy.deepcopy(row["scope"]),
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
    package["lineage"] = {
        "kind": "SUCCESSOR",
        "predecessor_run_id": "run-synthetic-2026-10-27-001",
        "predecessor_package_sha256": "a" * 64,
    }
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
    package["lineage"] = {
        "kind": "SUCCESSOR",
        "predecessor_run_id": "run-synthetic-2026-10-27-001",
        "predecessor_package_sha256": "a" * 64,
    }
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
            "target_scope": copy.deepcopy(row["scope"]),
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
            "unresolved_liabilities": ["remote-operation-unknown"],
        }
    )
    row["transition"]["rollback"]["scope"]["deployment"]["deployment_id"] = "other-deployment"
    _seal(package)
    result = _validate(package)
    row_result = _result_row(result, "R08")
    assert result.to_dict()["disposition"] == "NO_GO"
    assert row_result["effective_status"] == "HELD"
    assert "ROLLBACK_UNVERIFIED" in row_result["blockers"]
    assert "ROLLBACK_SCOPE_MISMATCH" in row_result["blockers"]


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


def test_reconciliation_rejects_duplicate_matrix_ids_and_wrong_p0_contract(tmp_path, monkeypatch) -> None:
    from salience.acceptance import p1_external_evidence, _reconciliation

    p0 = json.loads(P0_PATH.read_text(encoding="utf-8"))
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    cases = []
    duplicate_matrix = copy.deepcopy(matrix)
    duplicate_matrix["requirements"].append(copy.deepcopy(duplicate_matrix["requirements"][0]))
    cases.append((copy.deepcopy(p0), duplicate_matrix))

    for change in ("rename", "duplicate_check", "release_gate", "summary_status"):
        invalid_p0 = copy.deepcopy(p0)
        if change == "rename":
            invalid_p0["external_checks"][0]["check"] = "renamed check"
        elif change == "duplicate_check":
            invalid_p0["external_checks"][1]["check"] = invalid_p0["external_checks"][0]["check"]
        elif change == "release_gate":
            invalid_p0["release_gate"] = "RG1"
        else:
            invalid_p0["RG0"] = "UNKNOWN"
        cases.append((invalid_p0, copy.deepcopy(matrix)))

    for index, (invalid_p0, invalid_matrix) in enumerate(cases):
        p0_path = tmp_path / f"p0-{index}.json"
        matrix_path = tmp_path / f"matrix-{index}.json"
        p0_path.write_text(json.dumps(invalid_p0), encoding="utf-8")
        matrix_path.write_text(json.dumps(invalid_matrix), encoding="utf-8")
        monkeypatch.setattr(
            p1_external_evidence,
            "_read_ledgers",
            lambda p0_path=p0_path, matrix_path=matrix_path: _reconciliation._read_ledgers(
                p0_path, matrix_path
            ),
        )
        monkeypatch.setattr(
            p1_external_evidence,
            "_load_matrix_safely",
            lambda matrix_path=matrix_path: json.loads(matrix_path.read_text(encoding="utf-8")),
        )
        result = _validate(_valid_package()).to_dict()
        assert result["structural_status"] == "INVALID"
        assert result["disposition"] == "NO_GO"


def test_deployment_artifacts_and_row_routes_resolve_to_authenticated_inventory() -> None:
    mutations = []
    package = _valid_package()
    package["manifest"]["deployment"]["services"][0]["artifact_id"] = "unlisted-service-artifact"
    mutations.append(package)

    package = _valid_package()
    package["manifest"]["deployment"]["workers"][0]["artifact_id"] = "unlisted-worker-artifact"
    mutations.append(package)

    package = _valid_package()
    package["manifest"]["deployment"]["workers"][0]["route_ids"] = [
        _row_by_id(package, "R01")["scope"]["route"]["route_id"]
    ]
    mutations.append(package)

    package = _valid_package()
    _row_by_id(package, "R01")["scope"]["route"]["route_tuple_sha256"] = "9" * 64
    mutations.append(package)

    for invalid in mutations:
        _seal(invalid)
        result = _validate(invalid).to_dict()
        assert result["structural_status"] == "INVALID"
        assert result["disposition"] == "NO_GO"


def test_authority_scope_and_execution_principals_bind_exactly_or_use_delegation() -> None:
    mutations = []
    package = _valid_package()
    _row_by_id(package, "R01")["owner"]["scope"]["account_id"] = "other-account"
    mutations.append((package, "AUTHORIZATION_SCOPE_MISMATCH"))

    package = _valid_package()
    _row_by_id(package, "R01")["operator"]["scope"]["tenant_id"] = "other-tenant"
    mutations.append((package, "AUTHORIZATION_SCOPE_MISMATCH"))

    package = _valid_package()
    _row_by_id(package, "R01")["independent_reviewer"]["scope"]["resource_ids"] = []
    mutations.append((package, "AUTHORIZATION_SCOPE_MISMATCH"))

    package = _valid_package()
    _row_by_id(package, "R01")["procedure"]["principal"] = "unapproved-principal"
    mutations.append((package, "PROCEDURE_PRINCIPAL_UNAUTHORIZED"))

    package = _valid_package()
    row = _row_by_id(package, "R08")
    transition = row["transition"]
    transition.update(
        {
            "mode": "PLANNED_TRANSITION",
            "change_ticket": "CHG-PRINCIPAL-SYNTHETIC",
            "target_scope": copy.deepcopy(row["scope"]),
            "expected_pre_version": "route-v1",
            "expected_post_version": "route-v2",
            "observed_pre_version": "route-v1",
            "observed_post_version": "route-v2",
            "actor": "unapproved-principal",
            "command_receipt": _ref("transition-command"),
            "mutation_ids": ["mutation-principal-synthetic"],
        }
    )
    transition["rollback"].update(
        {
            "status": "VERIFIED",
            "action_receipt": _ref("transition-rollback"),
            "post_state_sha256": "6" * 64,
        }
    )
    mutations.append((package, "TRANSITION_ACTOR_UNAUTHORIZED"))

    package = _valid_package()
    row = _row_by_id(package, "R01")
    row["procedure"]["principal"] = "delegated-principal"
    row["procedure"]["delegation"] = {
        "principal_id": "delegated-principal",
        "authorized_by": "operator-01",
        "scope": _authorization_scope(_route_binding(row["scope"]["route"])),
        "evidence_ref": _ref("exact-scope-delegation"),
    }
    _seal(package)
    delegated = _validate(package).to_dict()
    assert "PROCEDURE_PRINCIPAL_UNAUTHORIZED" not in delegated["rows"]["R01"]["blockers"]

    for invalid, expected_code in mutations:
        _seal(invalid)
        result = _validate(invalid).to_dict()
        assert result["disposition"] == "NO_GO"
        assert expected_code in result["rows"]["R01" if expected_code != "TRANSITION_ACTOR_UNAUTHORIZED" else "R08"]["blockers"]


def test_planned_transition_final_state_and_version_match_freshness_readback() -> None:
    for mismatch in ("state", "version"):
        package = _valid_package()
        row = _row_by_id(package, "R08")
        transition = row["transition"]
        transition.update(
            {
                "mode": "PLANNED_TRANSITION",
                "change_ticket": "CHG-FRESHNESS-SYNTHETIC",
                "target_scope": copy.deepcopy(row["scope"]),
                "expected_pre_version": "route-v1",
                "expected_post_version": "route-v2",
                "observed_pre_version": "route-v1",
                "observed_post_version": "route-v2",
                "actor": "operator-01",
                "command_receipt": _ref("freshness-transition-command"),
                "pre_state_sha256": "5" * 64,
                "post_state_sha256": "6" * 64,
                "mutation_ids": ["freshness-transition-001"],
                "final_reviewed_version": "route-v2",
            }
        )
        transition["rollback"].update(
            {
                "status": "VERIFIED",
                "action_receipt": _ref("freshness-transition-rollback"),
                "post_state_sha256": "6" * 64,
            }
        )
        row["freshness"]["final_reviewed_state_sha256"] = "6" * 64
        row["freshness"]["final_reviewed_version"] = "route-v3" if mismatch == "version" else "route-v2"
        if mismatch == "state":
            row["freshness"]["final_reviewed_state_sha256"] = "7" * 64
        _seal(package)
        result = _validate(package).to_dict()
        assert result["disposition"] == "NO_GO"
        assert "PLANNED_TRANSITION_FRESHNESS_MISMATCH" in result["rows"]["R08"]["blockers"]


def test_no_mutation_requires_empty_resolved_liabilities() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R01")
    row["transition"]["rollback"]["unresolved_liabilities"] = ["pending-side-effect-check"]
    _seal(package)
    result = _validate(package).to_dict()
    assert result["disposition"] == "NO_GO"
    assert "ROLLBACK_LIABILITY_UNRESOLVED" in result["rows"]["R01"]["blockers"]


def test_lineage_requires_explicit_root_or_predecessor_invalidation_history() -> None:
    package = _valid_package()
    package.pop("lineage")
    _seal(package)
    result = _validate(package).to_dict()
    assert result["structural_status"] == "INVALID"

    package = _valid_package()
    package["lineage"] = {
        "kind": "SUCCESSOR",
        "predecessor_run_id": "run-synthetic-2026-10-27-001",
        "predecessor_package_sha256": "a" * 64,
    }
    _seal(package)
    result = _validate(package).to_dict()
    assert result["structural_status"] == "INVALID"
    assert "LINEAGE_PREDECESSOR_INVALID" in _blocker_codes(_validate(package))


def test_all_evidence_references_bind_to_run_and_exact_candidate_scope() -> None:
    mutations = []
    package = _valid_package()
    _row_by_id(package, "R01")["raw_result"]["binding"]["candidate_commit"] = "f" * 40
    mutations.append(package)

    package = _valid_package()
    _row_by_id(package, "R08")["transition"]["rollback"]["post_state"]["binding"]["deployment_id"] = "other-deployment"
    mutations.append(package)

    package = _valid_package()
    _row_by_id(package, "R34")["observability"]["events"][0]["binding"]["environment_id"] = "other-environment"
    mutations.append(package)

    package = _valid_package()
    _row_by_id(package, "R03")["dependency_evidence"][0]["evidence_ref"]["binding"]["run_id"] = "other-run"
    mutations.append(package)

    package = _valid_package()
    package["manifest"]["route_manifest"]["inventory"][0]["evidence_ref"]["binding"]["resource_ids"] = ["other-resource"]
    mutations.append(package)

    for invalid in mutations:
        _seal(invalid)
        result = _validate(invalid).to_dict()
        assert result["structural_status"] == "INVALID"
        assert result["disposition"] == "NO_GO"
        assert "EVIDENCE_BINDING_MISMATCH" in _blocker_codes(_validate(invalid))


def test_primary_and_independent_readback_evidence_are_distinct() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R01")
    row["readback"] = copy.deepcopy(row["raw_result"])
    _seal(package)
    result = _validate(package).to_dict()
    assert "EVIDENCE_INDEPENDENCE_REQUIRED" in result["rows"]["R01"]["blockers"]

    package = _valid_package()
    row = _row_by_id(package, "R23F")
    row["negative_control"] = {
        "required": True,
        "expected_outcome": "DENIED",
        "observed_outcome": "DENIED",
        "raw_denial": _ref("same-denial-and-readback"),
        "independent_effect_readback": _ref("same-denial-and-readback"),
        "observed_call_count": 0,
        "observed_effect_count": 0,
    }
    _seal(package)
    result = _validate(package).to_dict()
    assert "EVIDENCE_INDEPENDENCE_REQUIRED" in result["rows"]["R23F"]["blockers"]


def test_secret_scan_covers_normalized_package_and_uri_components_without_echo(tmp_path) -> None:
    mutations = []
    for uri in (
        "https://user:password-value@evidence.example/object",
        "https://evidence.example/object?access_token=synthetic-value",
        "https://evidence.example/object#private_key=synthetic-value",
    ):
        package = _valid_package()
        package["manifest"]["candidate"]["signed_build_provenance"]["uri"] = uri
        mutations.append((package, "synthetic-value" if "synthetic-value" in uri else "password-value"))

    package = _valid_package()
    package["final_audit"]["private_key"] = "sensitive-key-name"
    mutations.append((package, "sensitive-key-name"))

    package = _valid_package()
    package["lineage"]["credential_value"] = "sensitive-lineage-value"
    mutations.append((package, "sensitive-lineage-value"))

    package = _valid_package()
    package["manifest"]["effect_state"]["no_effect_sink"]["aws_secret_access_key"] = "sensitive-aws-key-value"
    mutations.append((package, "sensitive-aws-key-value"))

    package = _valid_package()
    package["invalidation_records"] = [
        {
            "invalidation_id": "secret-invalidation",
            "sequence": 1,
            "source_run_id": "prior-run",
            "source_package_sha256": "a" * 64,
            "new_run_id": package["run_id"],
            "scope": "ROW",
            "source_row_id": "R01",
            "affected_row_ids": ["R01"],
            "reason": "Bearer synthetic-secret-token-value",
            "invalidated_at": CURRENT_TIME,
        }
    ]
    mutations.append((package, "synthetic-secret-token-value"))

    package = _valid_package()
    package["artifact_digest_index"][0]["uri"] = "https://evidence.example/index#token=synthetic-index-value"
    mutations.append((package, "synthetic-index-value"))

    for package, secret in mutations:
        result = _validate(package).to_dict()
        rendered = json.dumps(result, sort_keys=True)
        assert result["structural_status"] == "INVALID"
        assert "SECRET_FOUND_IN_EVIDENCE" in _blocker_codes(_validate(package))
        assert secret not in rendered

    package = _valid_package()
    secret = "cli-secret-marker-value-123456789"
    _row_by_id(package, "R01")["status"] = f"Bearer {secret}"
    _seal(package)
    package_path = tmp_path / "secret-package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check-p1-external-acceptance.py"),
            "--package",
            str(package_path),
            "--schema",
            str(ROOT / "docs/v4/p1-rg0-rg1-external-acceptance.schema.json"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "SECRET_FOUND_IN_EVIDENCE" in completed.stdout
    assert secret not in completed.stdout


def test_final_auditor_differs_from_every_row_authority() -> None:
    for role in ("owner", "operator", "independent_reviewer"):
        package = _valid_package()
        row = _row_by_id(package, "R01")
        package["final_audit"]["auditor"]["person_id"] = row[role]["person_id"]
        _seal(package)
        result = _validate(package).to_dict()
        assert result["disposition"] == "NO_GO"
        assert "FINAL_AUDITOR_NOT_INDEPENDENT" in _blocker_codes(_validate(package))


def test_not_run_and_held_rows_need_no_dummy_execution_evidence_but_pass_fail_stay_strict() -> None:
    execution_fields = (
        "dependencies",
        "dependency_evidence",
        "procedure",
        "raw_result",
        "readback",
        "normalized_checks",
        "freshness",
        "transition",
        "negative_control",
        "credential_state",
        "observability",
        "operator_declaration",
        "reviewer_decision",
    )
    for status in ("NOT RUN", "HELD"):
        package = _valid_package()
        row = _row_by_id(package, "R01")
        row["status"] = status
        row["reason"] = f"{status.lower()} pending an external prerequisite"
        row["missing_prerequisites"] = ["approved external environment access"]
        for field in execution_fields:
            row.pop(field, None)
        _seal(package)
        result = _validate(package).to_dict()
        assert result["structural_status"] == "VALID"
        assert result["disposition"] == "NO_GO"
        assert result["rows"]["R01"]["effective_status"] == status

    for status in ("PASS", "FAIL"):
        package = _valid_package()
        row = _row_by_id(package, "R01")
        row["status"] = status
        row.pop("raw_result")
        row.pop("readback")
        row.pop("transition")
        _seal(package)
        result = _validate(package).to_dict()
        assert result["structural_status"] == "INVALID"
        assert result["disposition"] == "NO_GO"

    package = _valid_package()
    row = _row_by_id(package, "R01")
    row["status"] = "NOT RUN"
    row["reason"] = "Missing approval"
    row.pop("missing_prerequisites", None)
    _seal(package)
    assert _validate(package).to_dict()["structural_status"] == "INVALID"


def test_scope_bindings_cover_manifest_axes_and_route_applicability() -> None:
    package = _valid_package()
    manifest = package["manifest"]
    manifest["environment"].update(
        {
            "stage": "external-qualification",
            "region": "eu-test-2",
            "data_jurisdiction": ["CA"],
        }
    )
    manifest["environment"]["account_identity"].update(
        {"provider": "cloud-provider-b", "repository_id": "repo-b"}
    )
    manifest["deployment"]["temporal"].update(
        {"target_alias": "temporal-b", "namespace": "salience-b"}
    )
    for row in package["rows"]:
        row["scope"]["environment"].update(
            {
                "stage": manifest["environment"]["stage"],
                "region": manifest["environment"]["region"],
                "data_jurisdiction": manifest["environment"]["data_jurisdiction"],
            }
        )
        row["scope"]["account"].update(
            {
                "provider": manifest["environment"]["account_identity"]["provider"],
                "repository_id": manifest["environment"]["account_identity"]["repository_id"],
            }
        )
        row["scope"]["deployment"].update(
            {
                "temporal_target_alias": manifest["deployment"]["temporal"]["target_alias"],
                "temporal_namespace": manifest["deployment"]["temporal"]["namespace"],
            }
        )
    _seal(package)
    axis_result = _validate(package)

    package = _valid_package()
    _row_by_id(package, "R01")["raw_result"] = copy.deepcopy(
        _row_by_id(package, "R02")["raw_result"]
    )
    _seal(package)
    route_result = _validate(package)

    assert axis_result.to_dict()["structural_status"] == "INVALID"
    assert "EVIDENCE_BINDING_MISMATCH" in _blocker_codes(axis_result)
    assert "AUTHORIZATION_SCOPE_MISMATCH" in _blocker_codes(axis_result)
    assert route_result.to_dict()["structural_status"] == "INVALID"
    assert "EVIDENCE_BINDING_MISMATCH" in _blocker_codes(route_result)

    package = _valid_package()
    package["rows"][0]["owner"]["scope"]["route_binding"] = {
        "applicability": "NOT_APPLICABLE",
        "reason": "Synthetic route applicability test",
        "accountable_owner_id": "owner-01",
    }
    _seal(package)
    applicability_result = _validate(package)
    assert applicability_result.to_dict()["structural_status"] == "INVALID"
    assert "SCHEMA_VALIDATION_FAILED" in _blocker_codes(applicability_result)


def test_planned_transition_and_rollback_bind_exact_row_scope() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R08")
    transition = row["transition"]
    transition.update(
        {
            "mode": "PLANNED_TRANSITION",
            "change_ticket": "CHG-EXACT-SCOPE-001",
            "target_scope": "another-route-or-region",
            "expected_pre_version": "route-v1",
            "expected_post_version": "route-v2",
            "observed_pre_version": "route-v1",
            "observed_post_version": "route-v2",
            "actor": "operator-01",
            "command_receipt": _ref("exact-scope-command"),
            "pre_state_sha256": "5" * 64,
            "post_state_sha256": "6" * 64,
            "mutation_ids": ["exact-scope-mutation-001"],
            "final_reviewed_version": "route-v2",
        }
    )
    row["freshness"]["source_state_sha256"] = "5" * 64
    row["freshness"]["final_reviewed_state_sha256"] = "6" * 64
    row["freshness"]["final_reviewed_version"] = "route-v2"
    transition["rollback"].update(
        {
            "status": "VERIFIED",
            "action_receipt": _ref("exact-scope-rollback"),
            "post_state_sha256": "6" * 64,
            "scope": copy.deepcopy(row["scope"]),
        }
    )
    _seal(package)
    result = _validate(package).to_dict()
    assert result["structural_status"] == "INVALID"
    assert "PLANNED_TRANSITION_SCOPE_MISMATCH" in _blocker_codes(_validate(package))

    package = _valid_package()
    row = _row_by_id(package, "R08")
    transition = row["transition"]
    transition.update(
        {
            "mode": "PLANNED_TRANSITION",
            "change_ticket": "CHG-MISSING-ROLLBACK-SCOPE-001",
            "target_scope": copy.deepcopy(row["scope"]),
            "expected_pre_version": "route-v1",
            "expected_post_version": "route-v2",
            "observed_pre_version": "route-v1",
            "observed_post_version": "route-v2",
            "actor": "operator-01",
            "command_receipt": _ref("missing-scope-command"),
            "pre_state_sha256": "5" * 64,
            "post_state_sha256": "6" * 64,
            "mutation_ids": ["missing-scope-mutation-001"],
            "final_reviewed_version": "route-v2",
        }
    )
    row["freshness"]["source_state_sha256"] = "5" * 64
    row["freshness"]["final_reviewed_state_sha256"] = "6" * 64
    row["freshness"]["final_reviewed_version"] = "route-v2"
    transition["rollback"].update(
        {
            "status": "VERIFIED",
            "action_receipt": _ref("missing-scope-rollback"),
            "post_state_sha256": "6" * 64,
            "scope": copy.deepcopy(row["scope"]),
        }
    )
    transition["rollback"]["scope"]["deployment"].pop("deployment_id")
    _seal(package)
    result = _validate(package).to_dict()
    assert result["structural_status"] == "INVALID"
    assert "ROLLBACK_SCOPE_MISMATCH" in _blocker_codes(_validate(package))


def test_final_audit_rechecks_use_new_independent_evidence() -> None:
    def configure_planned_transition(row: dict[str, Any]) -> None:
        transition = row["transition"]
        transition.update(
            {
                "mode": "PLANNED_TRANSITION",
                "change_ticket": "CHG-AUDIT-REUSE-001",
                "target_scope": copy.deepcopy(row["scope"]),
                "expected_pre_version": "route-v1",
                "expected_post_version": "route-v2",
                "observed_pre_version": "route-v1",
                "observed_post_version": "route-v2",
                "actor": "operator-01",
                "command_receipt": _ref("audit-reuse-command-receipt"),
                "pre_state_sha256": "5" * 64,
                "post_state_sha256": "6" * 64,
                "mutation_ids": ["audit-reuse-mutation-001"],
                "final_reviewed_version": "route-v2",
            }
        )
        row["freshness"].update(
            {
                "source_state_sha256": "5" * 64,
                "final_reviewed_state_sha256": "6" * 64,
                "final_reviewed_version": "route-v2",
            }
        )
        transition["rollback"].update(
            {
                "status": "VERIFIED",
                "action_receipt": _ref("audit-reuse-rollback-action"),
                "post_state_sha256": "6" * 64,
                "scope": copy.deepcopy(row["scope"]),
            }
        )

    def configure_negative_control(row: dict[str, Any]) -> None:
        row["negative_control"].update(
            {
                "required": True,
                "expected_outcome": "DENIED",
                "observed_outcome": "DENIED",
                "raw_denial": _ref("audit-reuse-negative-denial"),
                "independent_effect_readback": _ref("audit-reuse-zero-effect"),
                "observed_call_count": 0,
                "observed_effect_count": 0,
            }
        )

    candidate_references: list[tuple[str, tuple[str, ...]]] = []
    planned_package = _valid_package()
    planned_row = _row_by_id(planned_package, "R08")
    configure_planned_transition(planned_row)
    candidate_references.extend(
        ("R08", path) for path, _ in _reference_paths(planned_row)
    )

    negative_package = _valid_package()
    negative_row = _row_by_id(negative_package, "R23F")
    configure_negative_control(negative_row)
    candidate_references.extend(
        ("R23F", path) for path, _ in _reference_paths(negative_row)
    )

    accepted_reuses: list[tuple[str, tuple[str, ...]]] = []
    for row_id, path in candidate_references:
        package = _valid_package()
        row = _row_by_id(package, row_id)
        if row_id == "R08":
            configure_planned_transition(row)
        else:
            configure_negative_control(row)
        recheck = next(
            item
            for item in package["final_audit"]["row_rechecks"]
            if item["row_id"] == row_id
        )
        evidence = row
        for key in path:
            evidence = evidence[int(key)] if isinstance(evidence, list) else evidence[key]
        recheck["status"] = "PASS"
        recheck["readback"] = copy.deepcopy(evidence)
        recheck["readback"]["evidence_id"] = (
            f"audit-reuse-spoof-{row_id}-{len(accepted_reuses)}"
        )
        recheck["readback"].setdefault("provenance", {})["source_id"] = (
            f"audit-source-spoof-{row_id}-{len(accepted_reuses)}"
        )
        _seal(package)
        result = _validate(package)
        if (
            result.to_dict()["structural_status"] != "INVALID"
            or "FINAL_AUDIT_RECHECK_NOT_INDEPENDENT" not in _blocker_codes(result)
        ):
            accepted_reuses.append((row_id, path))

    assert not accepted_reuses, f"final audit reused row evidence: {accepted_reuses!r}"


def test_fail_status_requires_an_executed_failed_normalized_assertion() -> None:
    package = _valid_package()
    row = _row_by_id(package, "R01")
    row["status"] = "FAIL"
    assert all(check["status"] == "PASS" for check in row["normalized_checks"])
    _seal(package)
    result = _validate(package).to_dict()
    assert result["structural_status"] == "INVALID"
    assert "FAIL_WITHOUT_FAILED_ASSERTION" in result["rows"]["R01"]["blockers"]
