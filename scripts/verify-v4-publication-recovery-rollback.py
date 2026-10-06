#!/usr/bin/env python3
"""Read-only pre-restart verification for both publication recovery holds."""

import argparse
import json
from pathlib import Path
import sys
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from salience.cycles.publication_recovery import (  # noqa: E402
    verify_publication_recovery_rollback,
)


REASONS = ("expired_permit_receipt", "published_during_cancel")


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise argparse.ArgumentTypeError("valid UUID required") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--workspace-id", required=True, type=_uuid)
    parser.add_argument("--expired-operation-id", required=True, type=_uuid)
    parser.add_argument("--cancel-operation-id", required=True, type=_uuid)
    arguments = parser.parse_args(argv)

    operation_ids = {
        REASONS[0]: arguments.expired_operation_id,
        REASONS[1]: arguments.cancel_operation_id,
    }
    try:
        holds = verify_publication_recovery_rollback(
            arguments.database_url,
            workspace_id=arguments.workspace_id,
            operation_ids=operation_ids,
        )
    except Exception:
        print("publication recovery rollback verification failed", file=sys.stderr)
        return 1

    payload = {
        "workspace_id": str(arguments.workspace_id),
        "reasons": list(REASONS),
        "operation_ids": {
            reason: str(operation_ids[reason]) for reason in REASONS
        },
        "hold_ids": {
            reason: str(hold.id) for reason, hold in zip(REASONS, holds)
        },
        "verified": True,
    }
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
