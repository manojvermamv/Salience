from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]


DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "docs/v4/p1-rg0-rg1-external-acceptance.schema.json"


P0_RELEASE_PATH = PROJECT_ROOT / "docs/v4/p0-release.json"


P1_QUALIFICATION_MATRIX_PATH = PROJECT_ROOT / "docs/v4/p1-qualification-matrix.json"


ROW_GATES: dict[str, str] = {
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


RG0_ROW_IDS = tuple(row_id for row_id, gate in ROW_GATES.items() if gate == "RG0")


RG1_ROW_IDS = tuple(row_id for row_id, gate in ROW_GATES.items() if gate == "RG1")


ROW_IDS = tuple(ROW_GATES)


REQUIRED_NEGATIVE_CONTROL_ROW_IDS = frozenset({"R22", "R16F", "R23F"})


FALLBACK_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "R02": ("P0", "R01"),
    "R03": ("P0", "R01"),
    "R05": ("P0", "R01"),
    "R06": ("R03", "R05"),
    "R07": ("R05", "R06"),
    "R08": ("R07",),
    "R09": ("R07", "R08"),
    "R10": ("R07",),
    "R13": ("R10",),
    "R15": ("P0", "R05"),
    "R17": ("R03", "R15"),
    "R18": ("R17",),
    "R32": ("R03", "R15"),
    "R33": ("R17", "R32"),
    "R34": ("R32", "R33"),
    "R35": ("R32",),
    "R53": ("P0", "R03"),
}


VALID_STATUSES = frozenset({"PASS", "FAIL", "NOT RUN", "HELD"})


PASS_WORDS = frozenset({"PASS"})


SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


SOURCE_SHA_RE = re.compile(r"^[a-f0-9]{40}$")


SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:password|secret_value|access_token|private_key|client_secret|"
    r"credential_value|api_key|authorization_value)(?:_|$)",
    re.IGNORECASE,
)


SECRET_VALUE_RE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/-]{12,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:gh[pousr]_[A-Za-z0-9]{24,}))"
)


@dataclass(frozen=True)
class RowDisposition:
    row_id: str
    claimed_status: str
    local_validation: str
    effective_status: str
    freshness: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "claimed_status": self.claimed_status,
            "local_validation": self.local_validation,
            "effective_status": self.effective_status,
            "freshness": self.freshness,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class AcceptanceDisposition:
    run_id: str | None
    structural_status: str
    disposition: str
    package_digest: str | None
    rows: Mapping[str, RowDisposition]
    blockers: tuple[Mapping[str, str], ...]
    ledger_status: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        from ._canonical import _canonical_value

        return {
            "run_id": self.run_id,
            "structural_status": self.structural_status,
            "disposition": self.disposition,
            "package_digest": self.package_digest,
            "rows": {
                row_id: self.rows[row_id].to_dict()
                for row_id in sorted(self.rows)
            },
            "blockers": [
                {"code": item["code"], "message": item["message"]}
                for item in sorted(
                    self.blockers,
                    key=lambda item: (item["code"], item["message"]),
                )
            ],
            "ledger_status": _canonical_value(self.ledger_status),
            "gate_state": "UNCHANGED",
        }


@dataclass
class _RowState:
    claimed_status: str
    freshness: str
    blockers: set[str]


@dataclass
class _ValidationState:
    global_blockers: dict[str, str]
    row_states: dict[str, _RowState]
    structural_errors: dict[str, str]

    def add_global(self, code: str, message: str, *, structural: bool = False) -> None:
        self.global_blockers.setdefault(code, message)
        if structural:
            self.structural_errors.setdefault(code, message)

    def add_row(self, row_id: str, code: str, message: str, *, structural: bool = False) -> None:
        if row_id in self.row_states:
            self.row_states[row_id].blockers.add(code)
        else:
            self.add_global(code, message, structural=structural)
        if structural:
            self.structural_errors.setdefault(code, message)


_LOCAL_BLOCKING_CODES = frozenset(
    {
        "NAMED_HUMAN_REQUIRED",
        "PERSON_ROLE_MISMATCH",
        "IDENTITY_NOT_VERIFIED",
        "AUTHORIZATION_SCOPE_MISSING",
        "AUTHORIZATION_SCOPE_MISMATCH",
        "AUTHORIZATION_REFERENCE_INVALID",
        "AUTHORIZATION_EXPIRED_OR_INVALID",
        "PERSON_ID_MISSING",
        "REVIEWER_OPERATOR_COLLISION",
        "REVIEWER_IDENTITY_MISMATCH",
        "OPERATOR_IDENTITY_MISMATCH",
        "SCOPE_IDENTITY_MISMATCH",
        "ROUTE_ID_NOT_UUID",
        "ROUTE_TUPLE_DIGEST_MISSING",
        "ROUTE_MANIFEST_DIGEST_MISSING",
        "RUNBOOK_NOT_PINNED",
        "PROCEDURE_NOT_EXECUTABLE",
        "COMMAND_ID_MISSING",
        "REQUEST_ID_MISSING",
        "PROCEDURE_KIND_INVALID",
        "PROCEDURE_CORRELATION_MISSING",
        "PROCEDURE_PRINCIPAL_UNAUTHORIZED",
        "PROCEDURE_TIME_INVALID",
        "RAW_RESULT_OR_READBACK_MISSING",
        "FRESHNESS_POLICY_INVALID",
        "FRESHNESS_OBSERVATION_INVALID",
        "STALE",
        "NO_MUTATION_HAS_MUTATION_IDS",
        "NO_MUTATION_STATE_MISMATCH",
        "NO_MUTATION_READBACK_MISSING",
        "TRANSITION_ACKNOWLEDGEMENT_MISSING",
        "TRANSITION_MODE_INVALID",
        "PLANNED_TRANSITION_UNPINNED",
        "TRANSITION_ACTOR_UNAUTHORIZED",
        "PLANNED_TRANSITION_MISMATCH",
        "PLANNED_TRANSITION_RECEIPT_MISSING",
        "PLANNED_TRANSITION_MUTATION_ID_MISSING",
        "ROLLBACK_UNVERIFIED",
        "ROLLBACK_EVIDENCE_MISSING",
        "ROLLBACK_ACTION_READBACK_MISSING",
        "ROLLBACK_POST_STATE_MISMATCH",
        "ROLLBACK_LIABILITY_UNRESOLVED",
        "ROLLBACK_ACKNOWLEDGEMENT_MISSING",
        "ROLLBACK_SCOPE_MISMATCH",
        "UNPLANNED_REVISION_DRIFT",
        "NEGATIVE_CONTROL_DENIAL_UNPROVEN",
        "NEGATIVE_CONTROL_REQUIRED",
        "NEGATIVE_CONTROL_RAW_DENIAL_MISSING",
        "NEGATIVE_CONTROL_EFFECT_READBACK_MISSING",
        "NEGATIVE_CONTROL_EFFECT_OBSERVED",
        "REVOKED_CREDENTIAL_RESTORED",
        "OBSERVABILITY_CORRELATION_MISMATCH",
        "OBSERVABILITY_CORRELATION_INCOMPLETE",
        "OBSERVABILITY_ARTIFACT_MISSING",
        "OBSERVABILITY_ACKNOWLEDGEMENT_MISSING",
        "SECRET_FREE_EVIDENCE_UNPROVEN",
        "SECRET_SCAN_EVIDENCE_MISSING",
        "SECRET_FOUND_IN_EVIDENCE",
        "DEPENDENCY_SET_MISMATCH",
        "DEPENDENCY_EVIDENCE_SET_MISMATCH",
        "DEPENDENCY_EVIDENCE_MISMATCH",
        "EVIDENCE_CLASS_NOT_EXTERNAL",
        "EVIDENCE_REFERENCE_INVALID",
        "EVIDENCE_BINDING_MISMATCH",
        "EVIDENCE_PROVENANCE_INVALID",
        "EVIDENCE_ID_COLLISION",
        "EVIDENCE_INDEPENDENCE_REQUIRED",
        "RESULT_STATUS_MISMATCH",
        "ROW_RECORD_DIGEST_MISMATCH",
    }
)
