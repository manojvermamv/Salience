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
    mapped_ids = {
        item.get("artifact_id")
        for item in mapping
        if isinstance(item, Mapping)
    }
    if mapped_ids != artifact_ids:
        state.add_global(
            "CANDIDATE_ARTIFACT_MAPPING_INCOMPLETE",
            "The candidate-to-artifact mapping must cover every exact artifact.",
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
