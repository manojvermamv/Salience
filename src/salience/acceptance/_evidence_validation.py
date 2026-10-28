from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ._contracts import _ValidationState


_SENSITIVE_KEY_NAMES = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "secret",
        "secret_value",
        "private_key",
        "client_secret",
        "credential",
        "credential_value",
        "api_key",
        "api_token",
        "access_token",
        "refresh_token",
        "bearer_token",
        "authorization",
        "authorization_value",
        "authorization_header",
        "session_token",
        "token",
    }
)
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/-]{12,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{24,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{12,})\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)"
)


def _is_sensitive_key(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    if normalized in {"secret_scan", "secret_scan_status", "secret_free"}:
        return False
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    sensitive_suffixes = (
        "_password",
        "_passwd",
        "_passphrase",
        "_secret",
        "_secret_value",
        "_private_key",
        "_client_secret",
        "_credential",
        "_credential_value",
        "_api_key",
        "_access_token",
        "_refresh_token",
        "_bearer_token",
        "_session_token",
        "_authorization",
        "_authorization_value",
        "_authorization_header",
        "_token",
    )
    return normalized.endswith(sensitive_suffixes) or (
        "secret" in normalized.split("_") and normalized.endswith("_key")
    )


def _contains_secret(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _is_sensitive_key(key) or _contains_secret(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(child) for child in value)
    if not isinstance(value, str):
        return False
    if _CREDENTIAL_VALUE_RE.search(value):
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.username is not None or parsed.password is not None:
        return True
    for encoded in (parsed.query, parsed.fragment):
        if not encoded:
            continue
        if _CREDENTIAL_VALUE_RE.search(encoded):
            return True
        for key, content in parse_qsl(encoded, keep_blank_values=True):
            if _is_sensitive_key(key) or _CREDENTIAL_VALUE_RE.search(content):
                return True
    return False


def _reference_paths(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Mapping[str, Any]]]:
    references: list[tuple[tuple[str, ...], Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        if {"uri", "sha256", "retention_mode"}.issubset(value):
            references.append((path, value))
        for key, child in value.items():
            references.extend(_reference_paths(child, (*path, str(key))))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            references.extend(_reference_paths(child, (*path, str(index))))
    return references


def _expected_binding(payload: Mapping[str, Any]) -> dict[str, Any]:
    manifest = payload.get("manifest", {})
    candidate = manifest.get("candidate", {})
    deployment = manifest.get("deployment", {})
    environment = manifest.get("environment", {})
    account = environment.get("account_identity", {})
    return {
        "run_id": payload.get("run_id"),
        "candidate_commit": candidate.get("source_commit"),
        "candidate_tree": candidate.get("source_tree"),
        "artifact_digests": sorted(candidate.get("candidate_artifact_digests", manifest.get("candidate_artifact_digests", []))),
        "deployment_id": deployment.get("deployment_id"),
        "environment_id": environment.get("environment_id"),
        "account_id": account.get("account_id"),
        "resource_ids": sorted(environment.get("resource_ids", [])),
    }


def _row_id_from_path(path: tuple[str, ...], payload: Mapping[str, Any]) -> str | None:
    if len(path) < 2 or path[0] != "rows":
        return None
    try:
        row = payload.get("rows", [])[int(path[1])]
    except (IndexError, TypeError, ValueError):
        return None
    if isinstance(row, Mapping) and isinstance(row.get("row_id"), str):
        return row["row_id"]
    return None


def _add_evidence_error(
    state: _ValidationState,
    payload: Mapping[str, Any],
    path: tuple[str, ...],
    code: str,
    message: str,
) -> None:
    row_id = _row_id_from_path(path, payload)
    if row_id is None:
        state.add_global(code, message, structural=True)
    else:
        state.add_row(row_id, code, message, structural=True)


def _independent(primary: Any, readback: Any) -> bool:
    if not isinstance(primary, Mapping) or not isinstance(readback, Mapping):
        return False
    primary_provenance = primary.get("provenance", {})
    readback_provenance = readback.get("provenance", {})
    return (
        primary.get("evidence_id") != readback.get("evidence_id")
        and primary.get("sha256") != readback.get("sha256")
        and isinstance(primary_provenance, Mapping)
        and isinstance(readback_provenance, Mapping)
        and primary_provenance.get("source_id") != readback_provenance.get("source_id")
        and primary_provenance.get("operator_id") != readback_provenance.get("operator_id")
    )


def _validate_evidence_bindings(
    payload: Mapping[str, Any],
    state: _ValidationState,
) -> None:
    expected = _expected_binding(payload)
    identities: dict[str, tuple[Any, ...]] = {}
    for path, reference in _reference_paths(payload):
        if path and path[0] == "artifact_digest_index":
            continue
        binding = reference.get("binding")
        provenance = reference.get("provenance")
        evidence_id = reference.get("evidence_id")
        if (
            not isinstance(binding, Mapping)
            or dict(binding) != expected
        ):
            _add_evidence_error(
                state,
                payload,
                path,
                "EVIDENCE_BINDING_MISMATCH",
                "An evidence reference does not bind to this run and exact candidate/deployment/environment/account/resource scope.",
            )
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or not isinstance(provenance, Mapping)
            or not isinstance(provenance.get("source_id"), str)
            or not provenance.get("source_id")
            or not isinstance(provenance.get("operator_id"), str)
            or not provenance.get("operator_id")
        ):
            _add_evidence_error(
                state,
                payload,
                path,
                "EVIDENCE_PROVENANCE_INVALID",
                "Every immutable evidence reference requires an ID and named source/operator provenance.",
            )
        elif evidence_id in identities:
            identity = (
                reference.get("uri"),
                reference.get("sha256"),
                dict(binding) if isinstance(binding, Mapping) else None,
                dict(provenance),
            )
            if identities[evidence_id] != identity:
                _add_evidence_error(
                    state,
                    payload,
                    path,
                    "EVIDENCE_ID_COLLISION",
                    "An evidence ID cannot refer to different immutable content or provenance.",
                )
        else:
            identities[evidence_id] = (
                reference.get("uri"),
                reference.get("sha256"),
                dict(binding) if isinstance(binding, Mapping) else None,
                dict(provenance) if isinstance(provenance, Mapping) else None,
            )

    manifest = payload.get("manifest", {})
    route_inventory = manifest.get("route_manifest", {}).get("inventory", [])
    if isinstance(route_inventory, list):
        for index, route in enumerate(route_inventory):
            if isinstance(route, Mapping) and dict(route.get("binding", {})) != expected:
                _add_evidence_error(
                    state,
                    payload,
                    ("manifest", "route_manifest", "inventory", str(index)),
                    "EVIDENCE_BINDING_MISMATCH",
                    "A route observation does not bind to this run and exact candidate/deployment/environment/account/resource scope.",
                )

    for index, row in enumerate(payload.get("rows", [])):
        if not isinstance(row, Mapping):
            continue
        execution_record = row.get("status") in {"PASS", "FAIL"}
        row_path = ("rows", str(index))
        observations = [
            (row.get("freshness"), (*row_path, "freshness")),
            (
                row.get("observability", {}).get("correlation"),
                (*row_path, "observability", "correlation"),
            ),
        ]
        dependencies = row.get("dependency_evidence", [])
        if isinstance(dependencies, list):
            observations.extend(
                (item, (*row_path, "dependency_evidence", str(dep_index)))
                for dep_index, item in enumerate(dependencies)
            )
        for observation, path in observations:
            if observation is None and not execution_record:
                continue
            if not isinstance(observation, Mapping) or dict(observation.get("binding", {})) != expected:
                _add_evidence_error(
                    state,
                    payload,
                    path,
                    "EVIDENCE_BINDING_MISMATCH",
                    "An evidence observation does not bind to this run and exact candidate/deployment/environment/account/resource scope.",
                )

    invalidations = payload.get("invalidation_records", [])
    if isinstance(invalidations, list):
        for index, record in enumerate(invalidations):
            if not isinstance(record, Mapping) or dict(record.get("binding", {})) != expected:
                _add_evidence_error(
                    state,
                    payload,
                    ("invalidation_records", str(index)),
                    "EVIDENCE_BINDING_MISMATCH",
                    "An invalidation observation does not bind to this run and exact candidate/deployment/environment/account/resource scope.",
                )

    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("row_id"), str):
            continue
        row_id = row["row_id"]
        raw_result = row.get("raw_result")
        readback = row.get("readback")
        if (
            row.get("status") in {"PASS", "FAIL"}
            or (isinstance(raw_result, Mapping) and isinstance(readback, Mapping))
        ) and not _independent(raw_result, readback):
            state.add_row(
                row_id,
                "EVIDENCE_INDEPENDENCE_REQUIRED",
                "Primary raw result and independent readback need distinct content, IDs, sources, and operators.",
                structural=True,
            )
        control = row.get("negative_control", {})
        denial = control.get("raw_denial") if isinstance(control, Mapping) else None
        effect_readback = (
            control.get("independent_effect_readback") if isinstance(control, Mapping) else None
        )
        if (
            isinstance(control, Mapping)
            and control.get("required")
            and (
                row.get("status") in {"PASS", "FAIL"}
                or (isinstance(denial, Mapping) and isinstance(effect_readback, Mapping))
            )
            and not _independent(denial, effect_readback)
        ):
            state.add_row(
                row_id,
                "EVIDENCE_INDEPENDENCE_REQUIRED",
                "Negative-control denial and zero-effect readback need distinct content, IDs, sources, and operators.",
                structural=True,
            )
