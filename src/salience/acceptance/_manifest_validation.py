from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ._canonical import (
    _evidence_ref_error,
    _is_sha256,
    _package_digest_payload,
    _parse_datetime,
    sha256_json,
)
from ._contracts import _ValidationState


def _validate_manifest(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    state: _ValidationState,
) -> None:
    if _parse_datetime(manifest.get("created_at")) is None:
        state.add_global(
            "MANIFEST_TIME_INVALID",
            "The manifest creation timestamp must be UTC.",
            structural=True,
        )
    if sha256_json(manifest) != payload.get("manifest_sha256"):
        state.add_global(
            "MANIFEST_DIGEST_MISMATCH",
            "The manifest digest does not match canonical manifest JSON.",
            structural=True,
        )
    signature = payload.get("manifest_signature", {})
    if (
        not isinstance(signature, Mapping)
        or signature.get("signed_manifest_sha256") != payload.get("manifest_sha256")
        or _evidence_ref_error(signature.get("signature_evidence"))
    ):
        state.add_global(
            "MANIFEST_SIGNATURE_REFERENCE_INVALID",
            "The manifest signing record is missing or not bound to the manifest digest.",
            structural=True,
        )

    candidate = manifest.get("candidate", {})
    artifacts = candidate.get("artifacts", [])
    artifact_ids: set[str] = set()
    artifacts_by_id: dict[str, Mapping[str, Any]] = {}
    artifact_digests: list[str] = []
    artifact_kinds: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            state.add_global("ARTIFACT_RECORD_INVALID", "Artifact record must be an object.", structural=True)
            continue
        artifact_id = artifact.get("artifact_id")
        if not isinstance(artifact_id, str) or artifact_id in artifact_ids:
            state.add_global("ARTIFACT_ID_DUPLICATE", "Artifact IDs must be unique.", structural=True)
        artifact_ids.add(str(artifact_id))
        if isinstance(artifact_id, str):
            artifacts_by_id[artifact_id] = artifact
        digest = artifact.get("sha256")
        artifact_digests.append(str(digest))
        artifact_kinds.add(str(artifact.get("kind")))
        evidence_ref = artifact.get("evidence_ref")
        if _evidence_ref_error(evidence_ref) or (
            isinstance(evidence_ref, Mapping) and evidence_ref.get("sha256") != digest
        ):
            state.add_global(
                "ARTIFACT_DIGEST_MISMATCH",
                f"Artifact {artifact_id} is not bound to its immutable digest reference.",
                structural=True,
            )
    required_kinds = {
        "OCI_IMAGE",
        "WHEEL",
        "LAUNCHER",
        "CONFIG_BUNDLE",
        "SOURCE_LOCK",
        "ARTIFACT_LOCK",
    }
    if not required_kinds.issubset(artifact_kinds):
        state.add_global(
            "CANDIDATE_ARTIFACT_SET_INCOMPLETE",
            "Candidate must bind image, wheel, launcher, config, source lock, and artifact lock.",
            structural=True,
        )
    if sorted(artifact_digests) != sorted(manifest.get("candidate_artifact_digests", [])):
        state.add_global(
            "CANDIDATE_ARTIFACT_INDEX_MISMATCH",
            "Candidate artifact digests do not match the manifest digest inventory.",
            structural=True,
        )
    mapping = candidate.get("artifact_mapping", [])
    mapped_ids: list[Any] = []
    mapped_components: list[Any] = []
    mapping_by_component: dict[str, str] = {}
    for item in mapping:
        if not isinstance(item, Mapping):
            continue
        component = item.get("component")
        artifact_id = item.get("artifact_id")
        mapped_ids.append(artifact_id)
        mapped_components.append(component)
        if isinstance(component, str) and isinstance(artifact_id, str):
            mapping_by_component[component] = artifact_id
            artifact = artifacts_by_id.get(artifact_id)
            if artifact is None or artifact.get("name") != component:
                state.add_global(
                    "CANDIDATE_ARTIFACT_MAPPING_INVALID",
                    "Each candidate component must resolve to its authenticated artifact record.",
                    structural=True,
                )
    if (
        set(mapped_ids) != artifact_ids
        or len(mapped_ids) != len(artifact_ids)
        or len(set(mapped_components)) != len(mapped_components)
    ):
        state.add_global(
            "CANDIDATE_ARTIFACT_MAPPING_INCOMPLETE",
            "The candidate-to-artifact mapping must cover every exact artifact once.",
            structural=True,
        )

    deployment = manifest.get("deployment", {})
    deployment_started = _parse_datetime(deployment.get("started_at"))
    deployment_completed = _parse_datetime(deployment.get("completed_at"))
    if (
        deployment.get("database", {}).get("schema_head") != candidate.get("migration_head")
        or deployment_started is None
        or deployment_completed is None
        or deployment_completed < deployment_started
        ):
        state.add_global(
            "DEPLOYMENT_IDENTITY_MISMATCH",
            "Deployment schema head or readback interval does not match the candidate.",
            structural=True,
        )
    _validate_deployment_artifact_and_route_bindings(
        payload,
        manifest,
        artifacts_by_id,
        mapping_by_component,
        state,
    )
    if manifest.get("effect_state", {}).get("production_effects_enabled") is not False:
        state.add_global(
            "PRODUCTION_EFFECTS_MUST_REMAIN_DISABLED",
            "The package must record production effects disabled.",
        )
    if manifest.get("effect_state", {}).get("publication_budget") != 0:
        state.add_global(
            "PUBLICATION_BUDGET_MUST_BE_ZERO",
            "The package must record a zero publication budget.",
        )
    if manifest.get("package_scope") not in {"SYNTHETIC", "EXTERNAL_PRODUCTION"}:
        state.add_global(
            "PACKAGE_SCOPE_INVALID",
            "Package scope must be explicit.",
            structural=True,
        )


def _validate_deployment_artifact_and_route_bindings(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    artifacts_by_id: Mapping[str, Mapping[str, Any]],
    mapping_by_component: Mapping[str, str],
    state: _ValidationState,
) -> None:
    deployment = manifest.get("deployment", {})
    service_ids: set[str] = set()
    worker_by_id: dict[str, Mapping[str, Any]] = {}
    for field, identity_field in (("services", "service_id"), ("workers", "worker_id")):
        records = deployment.get(field, [])
        if not isinstance(records, list):
            state.add_global("DEPLOYMENT_ARTIFACT_BINDING_INVALID", f"Deployment {field} must be an array.", structural=True)
            continue
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, Mapping):
                state.add_global("DEPLOYMENT_ARTIFACT_BINDING_INVALID", f"Deployment {field} entries must be objects.", structural=True)
                continue
            identity = record.get(identity_field)
            component = record.get("component")
            artifact_id = record.get("artifact_id")
            if (
                not isinstance(identity, str)
                or not identity
                or identity in seen
                or not isinstance(component, str)
                or not isinstance(artifact_id, str)
                or artifact_id not in artifacts_by_id
                or mapping_by_component.get(component) != artifact_id
            ):
                state.add_global(
                    "DEPLOYMENT_ARTIFACT_BINDING_INVALID",
                    f"Every deployed {field[:-1]} must resolve by component and artifact ID to the authenticated candidate set.",
                    structural=True,
                )
            if isinstance(identity, str):
                seen.add(identity)
                if field == "workers":
                    worker_by_id[identity] = record

    route_manifest = manifest.get("route_manifest", {})
    manifest_digest = route_manifest.get("authenticated_manifest_sha256")
    route_records = route_manifest.get("inventory", [])
    route_by_id: dict[str, Mapping[str, Any]] = {}
    if not isinstance(route_records, list):
        route_records = []
    for route in route_records:
        if not isinstance(route, Mapping):
            state.add_global("ROUTE_INVENTORY_INVALID", "Authenticated route inventory entries must be objects.", structural=True)
            continue
        route_id = route.get("route_id")
        worker_id = route.get("worker_id")
        if (
            not isinstance(route_id, str)
            or route_id in route_by_id
            or not isinstance(worker_id, str)
            or worker_id not in worker_by_id
            or not _is_sha256(route.get("route_tuple_sha256"))
            or route.get("authenticated_route_manifest_sha256") != manifest_digest
            or _evidence_ref_error(route.get("evidence_ref"))
        ):
            state.add_global(
                "ROUTE_INVENTORY_INVALID",
                "Each deployed route must be unique and bound to an authenticated worker route manifest.",
                structural=True,
            )
        if isinstance(route_id, str):
            route_by_id[route_id] = route

    for worker_id, worker in worker_by_id.items():
        declared = worker.get("route_ids", [])
        assigned = [
            route_id
            for route_id, route in route_by_id.items()
            if route.get("worker_id") == worker_id
        ]
        if (
            not isinstance(declared, list)
            or len(declared) != len(set(declared))
            or set(declared) != set(assigned)
        ):
            state.add_global(
                "WORKER_ROUTE_INVENTORY_MISMATCH",
                "Worker route IDs must equal the authenticated route inventory assigned to that worker.",
                structural=True,
            )

    for row in payload.get("rows", []):
        if not isinstance(row, Mapping):
            continue
        row_id = row.get("row_id")
        route = row.get("scope", {}).get("route", {})
        route_id = route.get("route_id")
        inventory_route = route_by_id.get(route_id)
        if (
            inventory_route is None
            or route.get("route_tuple_sha256") != inventory_route.get("route_tuple_sha256")
            or route.get("authenticated_route_manifest_sha256") != manifest_digest
            or route.get("authenticated_route_manifest_sha256")
            != inventory_route.get("authenticated_route_manifest_sha256")
            or route_id not in worker_by_id.get(inventory_route.get("worker_id"), {}).get("route_ids", [])
        ):
            if isinstance(row_id, str):
                state.add_row(
                    row_id,
                    "ROW_ROUTE_INVENTORY_MISMATCH",
                    f"Row {row_id} route does not resolve to an authenticated deployed worker route.",
                    structural=True,
                )
            else:
                state.add_global("ROW_ROUTE_INVENTORY_MISMATCH", "A row route is not in the authenticated worker inventory.", structural=True)


def _validate_record_digests(
    payload: Mapping[str, Any],
    row_by_id: Mapping[str, Mapping[str, Any]],
    state: _ValidationState,
) -> None:
    for row_id, row in row_by_id.items():
        recorded = row.get("record_sha256")
        body = {key: value for key, value in row.items() if key != "record_sha256"}
        if not _is_sha256(recorded) or sha256_json(body) != recorded:
            state.add_row(
                row_id,
                "ROW_RECORD_DIGEST_MISMATCH",
                f"Row {row_id} digest does not match its immutable contents.",
                structural=True,
            )
    try:
        expected = sha256_json(_package_digest_payload(payload))
    except (TypeError, ValueError):
        expected = None
    if expected != payload.get("package_digest"):
        state.add_global(
            "PACKAGE_DIGEST_MISMATCH",
            "The package digest does not match its manifest, rows, index, or invalidation lineage.",
            structural=True,
        )
    audit = payload.get("final_audit")
    if isinstance(audit, Mapping):
        body = {key: value for key, value in audit.items() if key != "record_sha256"}
        if (
            not _is_sha256(audit.get("record_sha256"))
            or sha256_json(body) != audit.get("record_sha256")
        ):
            state.add_global(
                "FINAL_AUDIT_DIGEST_MISMATCH",
                "Separate final-audit digest does not match its immutable contents.",
                structural=True,
            )
