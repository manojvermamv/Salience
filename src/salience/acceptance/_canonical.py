from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from ._contracts import PASS_WORDS, SHA256_RE


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


def _is_pass(value: Any) -> bool:
    return isinstance(value, str) and value.upper() in PASS_WORDS


def _schema_errors(payload: Any, schema: Any) -> list[tuple[str, str]]:
    try:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = list(validator.iter_errors(payload))
    except Exception as exc:  # Invalid schema is a caller-visible input failure.
        return [("SCHEMA_INVALID", str(exc))]
    normalized: list[tuple[str, str]] = []
    for error in errors:
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        normalized.append((path, error.message))
    return sorted(normalized)


def validate_payload_against_schema(payload: Any, schema_path: str | Path) -> list[str]:
    """Return deterministic schema errors without opening any external systems."""
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"SCHEMA_UNAVAILABLE: {exc}"]
    return [
        f"{path}: {message}"
        for path, message in _schema_errors(payload, schema)
    ]


def _evidence_ref_error(reference: Any) -> str | None:
    if not isinstance(reference, Mapping):
        return "EVIDENCE_REFERENCE_MISSING"
    uri = reference.get("uri")
    digest = reference.get("sha256")
    retention = reference.get("retention_mode")
    if not isinstance(uri, str) or not uri:
        return "EVIDENCE_URI_MISSING"
    if not _is_sha256(digest):
        return "EVIDENCE_DIGEST_INVALID"
    if retention == "CONTENT_ADDRESSED":
        expected_prefix = f"artifact://sha256/{digest}"
        if uri != expected_prefix:
            return "CONTENT_ADDRESS_DIGEST_MISMATCH"
    elif retention == "WORM_OBJECT_VERSION":
        if not isinstance(reference.get("object_version"), str) or not reference["object_version"]:
            return "IMMUTABLE_OBJECT_VERSION_MISSING"
        parsed = urlsplit(uri)
        if not parsed.scheme or not parsed.netloc:
            return "EVIDENCE_URI_INVALID"
    else:
        return "EVIDENCE_IMMUTABILITY_UNPROVEN"
    return None


def _iter_evidence_refs(value: Any) -> list[dict[str, str]]:
    found: dict[tuple[str, str, str, str], dict[str, str]] = {}

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            if {"uri", "sha256", "retention_mode"}.issubset(item):
                ref = {
                    "uri": str(item["uri"]),
                    "sha256": str(item["sha256"]),
                    "retention_mode": str(item["retention_mode"]),
                }
                object_version = item.get("object_version")
                if object_version is not None:
                    ref["object_version"] = str(object_version)
                found[
                    (
                        ref["uri"],
                        ref["sha256"],
                        ref["retention_mode"],
                        ref.get("object_version", ""),
                    )
                ] = ref
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return [found[key] for key in sorted(found)]


def _package_digest_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": payload.get("run_id"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "manifest_signature": payload.get("manifest_signature"),
        "artifact_digest_index": payload.get("artifact_digest_index"),
        "row_record_sha256": [
            row.get("record_sha256") for row in payload.get("rows", [])
            if isinstance(row, Mapping)
        ],
        "invalidation_records": payload.get("invalidation_records"),
    }
