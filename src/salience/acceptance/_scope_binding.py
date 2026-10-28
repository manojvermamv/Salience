from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _route_binding(route: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "applicability": "ROUTE",
        "route_id": route.get("route_id"),
        "route_tuple_sha256": route.get("route_tuple_sha256"),
        "authenticated_route_manifest_sha256": route.get(
            "authenticated_route_manifest_sha256"
        ),
    }


def _route_binding_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    scope = row.get("scope", {})
    route = scope.get("route", {})
    return _route_binding(route)


def _route_set_binding(manifest: Mapping[str, Any]) -> dict[str, Any]:
    route_manifest = manifest.get("route_manifest", {})
    inventory = route_manifest.get("inventory", [])
    route_bindings = [
        {
            "route_id": route.get("route_id"),
            "route_tuple_sha256": route.get("route_tuple_sha256"),
            "authenticated_route_manifest_sha256": route.get(
                "authenticated_route_manifest_sha256"
            ),
        }
        for route in inventory
        if isinstance(route, Mapping)
    ]
    return {
        "applicability": "ROUTE_SET",
        "routes": sorted(route_bindings, key=lambda item: str(item["route_id"])),
    }


def _scope_axes_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    candidate = manifest.get("candidate", {})
    deployment = manifest.get("deployment", {})
    environment = manifest.get("environment", {})
    account = environment.get("account_identity", {})
    database = deployment.get("database", {})
    temporal = deployment.get("temporal", {})
    return {
        "candidate_commit": candidate.get("source_commit"),
        "candidate_tree": candidate.get("source_tree"),
        "artifact_digests": sorted(
            candidate.get(
                "candidate_artifact_digests",
                manifest.get("candidate_artifact_digests", []),
            )
        ),
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
        "resource_ids": sorted(environment.get("resource_ids", [])),
        "data_jurisdiction": sorted(environment.get("data_jurisdiction", [])),
    }


def _authorization_scope_from_manifest(
    manifest: Mapping[str, Any],
    route_binding: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **_scope_axes_from_manifest(manifest),
        "route_binding": dict(route_binding),
    }


def _evidence_binding_from_manifest(
    payload: Mapping[str, Any],
    route_binding: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": payload.get("run_id"),
        **_scope_axes_from_manifest(payload.get("manifest", {})),
        "route_binding": dict(route_binding),
    }


def _route_binding_for_path(
    payload: Mapping[str, Any],
    path: Sequence[str],
) -> dict[str, Any]:
    manifest = payload.get("manifest", {})
    if (
        len(path) >= 4
        and path[0] == "manifest"
        and path[1:3] == ("route_manifest", "inventory")
    ):
        try:
            route = manifest["route_manifest"]["inventory"][int(path[3])]
        except (KeyError, IndexError, TypeError, ValueError):
            return {"applicability": "ROUTE", "route_id": None}
        if isinstance(route, Mapping):
            return _route_binding(route)
        return {"applicability": "ROUTE", "route_id": None}

    if len(path) >= 2 and path[0] == "rows":
        try:
            row = payload.get("rows", [])[int(path[1])]
        except (IndexError, TypeError, ValueError):
            return {"applicability": "ROUTE", "route_id": None}
        if isinstance(row, Mapping):
            return _route_binding_from_row(row)
        return {"applicability": "ROUTE", "route_id": None}

    if len(path) >= 3 and path[:2] == ("final_audit", "row_rechecks"):
        try:
            recheck = payload["final_audit"]["row_rechecks"][int(path[2])]
        except (KeyError, IndexError, TypeError, ValueError):
            return {"applicability": "ROUTE", "route_id": None}
        row_by_id = {
            row.get("row_id"): row
            for row in payload.get("rows", [])
            if isinstance(row, Mapping)
        }
        row = row_by_id.get(recheck.get("row_id")) if isinstance(recheck, Mapping) else None
        if isinstance(row, Mapping):
            return _route_binding_from_row(row)
        return {"applicability": "ROUTE", "route_id": None}

    if len(path) >= 2 and path[0] == "invalidation_records":
        try:
            record = payload.get("invalidation_records", [])[int(path[1])]
        except (IndexError, TypeError, ValueError):
            return _route_set_binding(manifest)
        if isinstance(record, Mapping) and record.get("scope") == "ROW":
            row_by_id = {
                row.get("row_id"): row
                for row in payload.get("rows", [])
                if isinstance(row, Mapping)
            }
            row = row_by_id.get(record.get("source_row_id"))
            if isinstance(row, Mapping):
                return _route_binding_from_row(row)
            return {"applicability": "ROUTE", "route_id": None}

    return _route_set_binding(manifest)
