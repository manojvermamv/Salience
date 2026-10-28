#!/usr/bin/env python3
"""Read the complete v2 rollback inventory through injected live authorities.

Production route and Temporal readers are deployment-owned capabilities. This
development candidate deliberately has no default external adapter and fails
closed when invoked as a stand-alone command without those capabilities.
"""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING
from uuid import UUID

from salience.cycles.p1_recovery_rollback import (
    RecoveryRollbackInventoryV2,
    verify_p1_recovery_rollback_v2,
)

if TYPE_CHECKING:
    from salience.cycles.production_routing import RouteLeaseAuthority
    from salience.workflows.worker import TemporalWorkerReadback


def verify(
    database_url: str,
    *,
    workspace_id: UUID,
    route_authority: RouteLeaseAuthority,
    temporal_readback: TemporalWorkerReadback,
) -> RecoveryRollbackInventoryV2:
    """Verify one SQL-discovered workspace against live route and Temporal readbacks."""
    return verify_p1_recovery_rollback_v2(
        database_url,
        workspace_id=workspace_id,
        route_authority=route_authority,
        temporal_readback=temporal_readback,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    arguments = parser.parse_args(argv)
    try:
        workspace_id = UUID(arguments.workspace_id)
    except ValueError as error:
        raise SystemExit("workspace scope must be a UUID") from error
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is unavailable")
    raise SystemExit(
        "live RouteLeaseAuthority and TemporalWorkerReadback adapters are not provisioned; "
        "the development-only candidate fails closed"
    )


if __name__ == "__main__":
    raise SystemExit(main())
