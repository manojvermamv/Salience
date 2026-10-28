#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from salience.acceptance.p1_external_evidence import (
    AcceptanceDisposition,
    validate_acceptance_package,
    validate_payload_against_schema,
)


def _invalid_result(code: str, message: str, *, run_id: object = None) -> dict[str, object]:
    return {
        "run_id": run_id if isinstance(run_id, str) else None,
        "structural_status": "INVALID",
        "disposition": "NO_GO",
        "package_digest": None,
        "rows": {},
        "blockers": [{"code": code, "message": message}],
        "ledger_status": {},
        "gate_state": "UNCHANGED",
    }


def _emit(result: dict[str, object]) -> None:
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    )


def _parse_json_file(path: Path, kind: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{kind} file cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{kind} file is invalid JSON: {exc}") from exc


class _MachineReadableArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _MachineReadableArgumentParser(
        description="Validate a local P1/RG0/RG1 external acceptance package."
    )
    parser.add_argument("--package", type=Path)
    parser.add_argument("--schema", type=Path)
    try:
        args = parser.parse_args(argv)
    except ValueError:
        _emit(_invalid_result("ARGUMENTS_INVALID", "CLI arguments are invalid."))
        return 2
    if args.package is None or args.schema is None:
        _emit(_invalid_result("ARGUMENTS_MISSING", "Both --package and --schema are required."))
        return 2

    try:
        payload = _parse_json_file(args.package, "package")
    except ValueError as exc:
        _emit(_invalid_result("PACKAGE_INPUT_INVALID", str(exc)))
        return 2

    schema_errors = validate_payload_against_schema(payload, args.schema)
    if schema_errors:
        _emit(
            {
                **_invalid_result(
                    "SCHEMA_VALIDATION_FAILED",
                    "Package does not satisfy the supplied schema.",
                    run_id=payload.get("run_id") if isinstance(payload, dict) else None,
                ),
                "schema_errors": sorted(schema_errors),
            }
        )
        return 2

    try:
        disposition: AcceptanceDisposition = validate_acceptance_package(
            payload,
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        # Invalid external data must produce a stable result rather than a traceback.
        _emit(
            _invalid_result(
                "PACKAGE_VALIDATION_ERROR",
                f"{type(exc).__name__}: {exc}",
                run_id=payload.get("run_id") if isinstance(payload, dict) else None,
            )
        )
        return 2

    result = disposition.to_dict()
    _emit(result)
    if disposition.structural_status == "INVALID":
        return 2
    if disposition.disposition == "NO_GO":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
