"""Read-only local qualification inventory; never exports payloads or credentials."""

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from salience.api.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    database = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(database, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '5s'")
        report = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "environment": "local fixture database; not production inventory",
            "migration": connection.execute("SELECT version_num FROM alembic_version").fetchone(),
            "tables": connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name").fetchall(),
            "jobs_by_state": connection.execute("SELECT state,count(*) FROM jobs GROUP BY state ORDER BY state").fetchall(),
            "active_history_references": connection.execute("SELECT id,workspace_id,workflow_run_id,state,trace_id FROM jobs WHERE state NOT IN ('succeeded','failed','cancelled','canceled','dead_letter') ORDER BY id").fetchall(),
            "external_references": connection.execute("SELECT id,job_id,provider_name,provider_reference,status FROM external_effects ORDER BY id").fetchall(),
            "pending_reservations": connection.execute("SELECT id,budget_id,job_id,reserved_amount,status,expires_at FROM budget_reservations WHERE status='reserved' ORDER BY id").fetchall(),
            "versions": {name: importlib.metadata.version(name) for name in ["temporalio", "sqlalchemy", "PyJWT", "cryptography", "boto3", "opentelemetry-sdk", "playwright"]},
            "legacy_fixture_routes": sorted(create_app(control_token="not-used").openapi()["paths"]),
            "limits": "Temporal history payload/replay, remote IDs inside provider edge stores and production pending liabilities require separate scoped inventories before cutover; no production endpoints were queried",
        }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({"migration": report["migration"], "tables": len(report["tables"]), "active_history_references": len(report["active_history_references"]), "external_references": len(report["external_references"]), "pending_reservations": len(report["pending_reservations"])}))


if __name__ == "__main__":
    main()
