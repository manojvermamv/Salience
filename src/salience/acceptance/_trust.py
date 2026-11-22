from __future__ import annotations

from base64 import b64decode
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ._canonical import _is_sha256, sha256_json
from ._contracts import _ValidationState


def _verify(public_key: bytes, signature: bytes, purpose: str, digest: object) -> None:
    if not _is_sha256(digest):
        raise ValueError("expected an immutable SHA-256 digest")
    message = f"salience-external-acceptance-v1.1:{purpose}:{digest}".encode("ascii")
    Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)


def _validate_external_trust(
    payload: Mapping[str, Any], trust: Mapping[str, Any] | None, state: _ValidationState
) -> bool:
    if not isinstance(trust, Mapping):
        state.add_global(
            "EXTERNAL_TRUST_UNAVAILABLE",
            "External acceptance requires an independently configured Ed25519 trust file.",
        )
        return False

    try:
        manifest = payload["manifest_signature"]
        attestation = payload["package_attestation"]
        producer_id = manifest["key_id"]
        if manifest["algorithm"] != "Ed25519" or attestation["key_id"] != producer_id:
            raise ValueError("manifest and package require the trusted producer key")
        producer_key = b64decode(trust["evidence_producers"][producer_id], validate=True)
        manifest_signature = b64decode(manifest["signature_b64"], validate=True)
        if sha256(manifest_signature).hexdigest() != manifest["signature_evidence"]["sha256"]:
            raise ValueError("manifest signature differs from the evidence reference")
        _verify(producer_key, manifest_signature, "manifest", payload["manifest_sha256"])
        package_signature = b64decode(attestation["signature_b64"], validate=True)
        _verify(producer_key, package_signature, "package", payload["package_digest"])

        if "final_audit" in payload:
            audit = payload["final_audit"]
            audit_attestation = audit["attestation"]
            auditor_id = audit["auditor"]["person_id"]
            if audit_attestation["key_id"] != auditor_id:
                raise ValueError("audit signature must identify the named auditor")
            auditor_key = b64decode(trust["final_auditors"][auditor_id], validate=True)
            if auditor_key == producer_key or auditor_id == producer_id:
                raise ValueError("audit requires a separate trusted key")
            body = {
                key: value for key, value in audit.items()
                if key not in {"attestation", "record_sha256"}
            }
            audit_signature = b64decode(audit_attestation["signature_b64"], validate=True)
            _verify(auditor_key, audit_signature, "audit", sha256_json(body))
    except (InvalidSignature, KeyError, TypeError, ValueError):
        state.add_global(
            "EXTERNAL_SIGNATURE_INVALID",
            "External manifest, evidence package and any final audit must have valid signatures under their trusted keys.",
        )
        return False
    return True
