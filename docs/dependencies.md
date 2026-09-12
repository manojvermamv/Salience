# Dependency Inventory

This is the review record for the initial `phases-1-4-foundation` branch. Exact Python dependency pins are copied from `pyproject.toml`; image digests are copied from `compose.yaml`.

| Component | Version selected | License | Boundary | Replacement path |
| --- | --- | --- | --- | --- |
| PostgreSQL | 17.9 | PostgreSQL License | canonical persistence | PostgreSQL-compatible migration/export |
| Temporal Server | 1.23.1.1 | MIT | `WorkflowBackend` | alternate durable backend + canonical job replay |
| Temporal Python SDK | 1.32.0 | MIT | `TemporalWorkflowBackend` | remove adapter |
| Garage | v2.3.0 | AGPL-3.0 | `S3ObjectStore` | alternate S3-compatible service |
| boto3 | 1.43.93 | Apache-2.0 | S3 transport only | any S3 client |
| FastAPI | 0.141.1 | MIT | HTTP transport | another ASGI/OpenAPI transport |
| SQLAlchemy | 2.0.52 | MIT | persistence repositories | SQL implementation behind repositories |
| Alembic | 1.20.0 | MIT | migrations | SQL migration scripts |
| asyncpg | 0.31.0 | Apache-2.0 | PostgreSQL transport | DBAPI behind repositories |
| psycopg[binary] | 3.3.5 | LGPL-3.0-only | Worker-side PostgreSQL boundary | Retain asyncpg for SQLAlchemy/migration paths; re-review LGPL terms before redistributing a bundled binary image |
| Pydantic | 2.12.5 | MIT | transport schemas | JSON-schema compatible types |
| Uvicorn | 0.40.0 | BSD-3-Clause | ASGI process | another ASGI server |
| JSON Schema | 4.26.0 | MIT | boundary validation | compliant validator |
| OpenTelemetry | 1.44.0 | Apache-2.0 | trace emission | compatible SDK/exporter |
| MCP Python SDK | 2.2.0 | MIT | MCP gateway | protocol client/server adapter |
| A2A Python SDK | 1.1.2 | Apache-2.0 | remote-agent gateway | protocol adapter |

## Review cadence

Review image and package advisories before every release. Garage, MCP, and A2A have explicit protocol/security caveats documented in their ADRs; version compatibility is tested rather than inferred.
